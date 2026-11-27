"""The platform's metric linter over the real output of a real worker (Observability Standard).

Evidence, not source reading: this runs ``worker.run`` (the production start-up path) against a
dev server on the real clock, drives the paths that emit each metric, scrapes **both** endpoints
(the ops app on ``HEALTH_PORT`` and the Temporal runtime on ``METRICS_BIND_ADDRESS``) and runs
``fastapicommon-metricslint check`` (then ``check --strict``) over each scrape.

Two things are pinned here on purpose, neither silenced by a flag:

* ``temporal_*`` (the SDK's own telemetry) is not an exempt prefix in the platform registry, so
  the linter reports every ``temporal_*`` family as an unclassified name. That is the named
  exception of ADR-0006; the test asserts it is *exactly* that (rule ``namespace``, prefix
  ``temporal_``) and that nothing else on either endpoint has a finding.
* ``--strict`` makes the platform's own still-``proposed`` ``platform_*`` metrics errors, so
  strict is run with ``--allow-proposed-owner`` for the library that owns them
  (``platform-gincommon``): strict about what this service emits, not about the library's.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fake_engine import FakeDb, FakeEngineApi, make_input
from fastapicommon.metrics.lint import main as metricslint
from harness import make_current
from llm_workers_generation import tuning
from llm_workers_generation import worker as worker_mod
from llm_workers_generation.config import Settings
from llm_workers_generation.observability.setup import build_runtime
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

pytestmark = pytest.mark.integration

#: The platform library that owns the platform_* metrics this process registers.
LIBRARY_OWNER = "platform-gincommon"
FINDING = re.compile(r"^(error|warning) \[([a-z-]+)\] (\S+?): ")

APP_FAMILIES = {
    "activity_duration_seconds",
    "activity_failures_total",
    "section_duration_seconds",
    "section_outcome_total",
    "slot_wait_seconds",
    "quota_suspensions_total",
    "run_gone_total",
    "gate_expired_total",
    "scope_revoked_total",
    "section_tokens_total",
    "retried_tokens_total",
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def lint(path: Path, *flags: str) -> tuple[int, list[tuple[str, str, str]], str]:
    """Run the real CLI: its exit code, the parsed findings (severity, rule, metric), its output."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = metricslint(["check", *flags, str(path)])
    findings = [m.groups() for line in out.getvalue().splitlines() if (m := FINDING.match(line))]
    return code, findings, out.getvalue()  # type: ignore[return-value]


def families(scrape: str) -> dict[str, set[str]]:
    """``{family: label names}`` of a scrape."""
    names: dict[str, set[str]] = {}
    for line in scrape.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        for suffix in ("_bucket", "_sum", "_count"):
            name = name.removesuffix(suffix)
        labels = set(re.findall(r'([a-z_]+)="', line.partition("{")[2]))
        names.setdefault(name, set()).update(labels)
    return names


async def test_both_endpoints_pass_the_platform_linter(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    api_holder: dict[str, Any] = {}

    async def fake_build() -> tuple[FakeEngineApi, SimpleNamespace]:
        async def aclose() -> None:
            return None

        api_holder["api"] = FakeEngineApi(FakeDb())
        return api_holder["api"], SimpleNamespace(aclose=aclose)

    monkeypatch.setattr(worker_mod, "build_rag_engine", fake_build)
    ops_port, sdk_port = free_port(), free_port()
    settings = Settings(
        worker_kind="generation",
        worker_tier="standard",
        app_env="test",
        build_id="lint-1",
        health_port=ops_port,
        metrics_bind_address=f"127.0.0.1:{sdk_port}",
    )
    env = await WorkflowEnvironment.start_local(runtime=build_runtime(settings))
    stop = asyncio.Event()
    started: list[Any] = []
    task = asyncio.create_task(worker_mod.run(settings, stop, env.client))
    try:
        await make_current(env.client, settings.deployment_name, "lint-1")

        async def run_one(wf_id: str, db: FakeDb, *, wait: bool = True, **over: Any) -> Any:
            api_holder["api"].db = db
            handle = await env.client.start_workflow(
                "GenerationWorkflow",
                make_input(**{"outline_gate": False, **over}),
                id=wf_id,
                task_queue=settings.work_queue,
            )
            started.append(handle)
            return await asyncio.wait_for(handle.result(), 90) if wait else handle

        # a document with one section that fails for good (activity failures, a failed section)
        assert (await run_one("lint-fail", FakeDb(behaviors={"3": ["inference"] * 3})))[
            "status"
        ] == "complete"
        # a weak verification redrafts the section (retried tokens)
        await run_one("lint-weak", FakeDb(behaviors={"verify:2": ["weak"] * 3}))
        # the run's rows are gone (run_gone_total)
        assert (await run_one("lint-gone", FakeDb(gone=True)))["status"] == "gone"
        # a quota refusal suspends the run (quota_suspensions_total). Its activities read the shared
        # fake, so the next run starts only once the suspension is out; then the run is ended so no
        # tuning patch below can change what a replay of it would do.
        quota = await run_one("lint-quota", FakeDb(behaviors={"2": ["quota"]}), wait=False)
        async with httpx.AsyncClient() as client:
            async with asyncio.timeout(60):
                while (
                    "llm_workers_quota_suspensions_total{"
                    not in (await client.get(f"http://127.0.0.1:{ops_port}/metrics")).text
                ):
                    await asyncio.sleep(0.2)
            await quota.terminate()

            # a lost approve signal, recovered when the row is read (reconcile_recovered_total)
            with monkeypatch.context() as tune:
                tune.setattr(tuning, "WAIT_RECONCILE_SECONDS", 2)
                db = FakeDb()
                gated = await run_one("lint-reconcile", db, wait=False, outline_gate=True)
                async with asyncio.timeout(60):
                    while db.status != "awaiting_outline_approval":
                        await asyncio.sleep(0.05)
                db.status, db.outline_state, db.stage = "generating", "approved", "stage1"
                assert (await asyncio.wait_for(gated.result(), 90))["status"] == "complete"

            # most of the ended stage 2 sections fail with one error type (poison_breaker_total)
            with monkeypatch.context() as tune:
                tune.setattr(tuning, "POISON_MIN_SECTIONS", 2)
                sections = [("0", "intro", True)] + [(str(i), "scope", False) for i in (1, 2, 3)]
                poisoned = FakeDb(
                    skeleton=sections, behaviors={str(i): ["inference"] * 3 for i in (1, 2, 3)}
                )
                assert (await run_one("lint-poison", poisoned))["failure_reason"] == (
                    "inference_unavailable"
                )

            # the terminal write cannot be recorded for the whole window (unrecorded_failures_total):
            # a database outage while ``fail_run`` runs. The window is 60 minutes; here 0.1 minute.
            with monkeypatch.context() as tune:
                tune.setattr(tuning, "TERMINAL_WRITE_RETRY_MINUTES", 0.1)

                async def down(ctx: Any, reason: str, stage: str | None) -> bool:
                    raise RuntimeError("database unreachable")

                outage = FakeDb(behaviors={"scope": ["scope"]})
                api_holder["api"].db = outage
                api_holder["api"].fail_run = down
                handle = await run_one("lint-unrecorded", outage, wait=False)
                with pytest.raises(WorkflowFailureError) as info:
                    await asyncio.wait_for(handle.result(), 90)
                assert info.value.cause.type == "UnrecordedFailure"  # type: ignore[union-attr]
                del api_holder["api"].fail_run  # back to the class's method

            # the run deadline fails a run (a workflow-side metric)
            with monkeypatch.context() as tune:
                tune.setattr(tuning, "RUN_MAX_SECONDS", -1)
                await run_one("lint-deadline", FakeDb(call_delay=0.0))
            ops = (await client.get(f"http://127.0.0.1:{ops_port}/metrics")).text
            sdk = (await client.get(f"http://127.0.0.1:{sdk_port}/metrics")).text
        for handle in started:  # nothing may outlive the test
            with contextlib.suppress(Exception):
                await handle.terminate()
    finally:
        stop.set()  # also when an assertion failed: a worker left polling would hang the shutdown
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 30)
        await env.shutdown()

    (tmp_path / "ops.txt").write_text(ops)
    (tmp_path / "sdk.txt").write_text(sdk)

    # -- the ops endpoint: the 11 activity-side metrics, every one with a series ---------------------
    ops_families = families(ops)
    app = {n for n in ops_families if n.startswith("llm_workers_")}
    assert app == {f"llm_workers_{n}" for n in APP_FAMILIES}
    for name in app:
        assert {"domain", "service", "environment"} <= ops_families[name], (
            name
        )  # injected centrally
    code, findings, text = lint(tmp_path / "ops.txt")
    assert code == 0, text
    assert [f for f in findings if f[0] == "error"] == []
    code, findings, text = lint(
        tmp_path / "ops.txt", "--strict", "--allow-proposed-owner", LIBRARY_OWNER
    )
    assert code == 0, text
    code, findings, _ = lint(tmp_path / "ops.txt", "--strict")  # the library's own proposals
    assert code == 1 and {f[1] for f in findings} == {"status"}
    assert all(f[2].startswith("platform_") for f in findings)  # none is a metric of ours

    # -- the Temporal endpoint: workflow-side metrics clean, SDK telemetry the named exception -------
    sdk_families = families(sdk)
    ours = {n for n in sdk_families if n.startswith("llm_workers_")}
    assert {
        "llm_workers_workflow_started_total",
        "llm_workers_workflow_completed_total",
        "llm_workers_workflow_duration_seconds",
        "llm_workers_workflow_history_events_total",
        "llm_workers_fanout_in_flight",
        "llm_workers_run_deadline_total",
        "llm_workers_reconcile_recovered_total",
        "llm_workers_poison_breaker_total",
        "llm_workers_unrecorded_failures_total",
    } <= ours
    sdk_native = {n for n in sdk_families if n.startswith("temporal_")}
    assert sdk_native and ours | sdk_native == set(sdk_families)  # nothing else is served here
    for name in ours | sdk_native:  # the central labels reach the SDK's own series too
        assert {"domain", "service", "environment"} <= sdk_families[name], name
    for flags in ((), ("--strict",)):
        code, findings, text = lint(tmp_path / "sdk.txt", *flags)
        assert code == 1  # the SDK's names are not in the registry: ADR-0006 named exception
        assert all(f[0] == "error" and f[1] == "namespace" for f in findings), text
        assert {f[2] for f in findings} == sdk_native, text  # exactly the SDK's, none of ours
