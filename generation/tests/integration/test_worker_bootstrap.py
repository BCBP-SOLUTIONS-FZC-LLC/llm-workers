"""The bootstrap: settings -> workers polling -> a workflow runs -> clean shutdown (LLD §3.4, §13.5)."""

import asyncio
import os
import signal
import socket
from types import SimpleNamespace

import httpx
import pytest
from fake_engine import FakeDb, FakeEngineApi, make_input
from llm_workers_generation import worker as worker_mod
from llm_workers_generation.config import Settings
from llm_workers_generation.observability.ops import OpsServerError, Readiness, start_ops
from llm_workers_generation.observability.setup import build_interceptors, build_runtime
from llm_workers_generation.operator.commands import Operator, drain_report, list_live_runs
from temporalio.testing import WorkflowEnvironment

pytestmark = pytest.mark.integration


async def test_run_polls_both_queues_and_shuts_down_cleanly(monkeypatch) -> None:  # noqa: ANN001
    db = FakeDb()
    closed = asyncio.Event()

    async def fake_build() -> tuple[FakeEngineApi, SimpleNamespace]:
        async def aclose() -> None:
            closed.set()

        return FakeEngineApi(db), SimpleNamespace(aclose=aclose)

    monkeypatch.setattr(worker_mod, "build_rag_engine", fake_build)
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        settings = Settings(
            worker_kind="extraction", worker_tier="standard", health_port=free_port(), build_id="t1"
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker_mod.run(settings, stop, env.client))
        await asyncio.sleep(0.5)
        h = await env.client.start_workflow(
            "ExtractionWorkflow",
            make_input(kind="extraction", outline_gate=False),
            id="boot-1",
            task_queue="llm-extraction",
        )
        assert (await h.result())["status"] == "complete"
        stop.set()
        await asyncio.wait_for(task, 20)
        assert closed.is_set()  # the engine was closed after the workers drained
    finally:
        await env.shutdown()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_ops_server_serves_health_probes_and_metrics(app_registry) -> None:  # noqa: ANN001
    from llm_workers_generation.observability.app_metrics import app_metrics

    app_metrics().slot_waited("standard", 1.0)
    readiness = Readiness()
    ops = await start_ops(readiness, 0, "127.0.0.1")
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{ops.port}") as c:
            assert (await c.get("/health")).status_code == 200
            assert (await c.get("/live")).status_code == 200
            assert (await c.get("/ready")).status_code == 503  # not polling yet
            readiness.ready = True
            assert (await c.get("/ready")).status_code == 200
            assert (await c.get("/nope")).status_code == 404
            metrics = await c.get("/metrics")
            assert metrics.status_code == 200
            assert 'llm_workers_slot_wait_seconds_count{domain="llm"' in metrics.text
            assert "platform_" in metrics.text  # fastapicommon's own collectors on the same page
    finally:
        await ops.stop()
        await ops.stop()  # safe twice


async def test_ops_server_bind_failure_surfaces_instead_of_hanging() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(OpsServerError):
            await asyncio.wait_for(
                start_ops(Readiness(), int(taken.getsockname()[1]), "127.0.0.1"), 10
            )


async def test_sigterm_drains_in_flight_work_and_stops_the_ops_server_last(monkeypatch) -> None:  # noqa: ANN001
    """LLD §13.5: on SIGTERM the pod leaves rotation (/ready 503), the in-flight activity finishes, then the
    ops server, the engine and the tracer stop, in that order. uvicorn must not take the signal."""
    db = FakeDb()
    events: list[str] = []

    async def slow_extraction(self, ctx):  # noqa: ANN001, ANN202
        db.tick("run_extraction")
        await asyncio.sleep(2.0)  # in flight while the stop signal arrives
        events.append("activity_finished")
        return await FakeEngineApi.run_extraction_fast(self, ctx)  # type: ignore[attr-defined]

    FakeEngineApi.run_extraction_fast = FakeEngineApi.run_extraction  # type: ignore[attr-defined]
    monkeypatch.setattr(FakeEngineApi, "run_extraction", slow_extraction)

    async def fake_build() -> tuple[FakeEngineApi, SimpleNamespace]:
        async def aclose() -> None:
            events.append("engine_closed_called")

        return FakeEngineApi(db), SimpleNamespace(aclose=aclose)

    monkeypatch.setattr(worker_mod, "build_rag_engine", fake_build)

    class RecordingLog:
        def info(self, event: str, **fields: object) -> None:
            events.append(event)

        warning = info

    monkeypatch.setattr(worker_mod, "log", RecordingLog())
    port = free_port()
    env = await WorkflowEnvironment.start_time_skipping()
    handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        settings = Settings(
            worker_kind="extraction", worker_tier="standard", health_port=port, build_id="t1"
        )
        task = asyncio.create_task(worker_mod.run(settings, None, env.client))  # installs SIGTERM
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            for _ in range(100):  # wait until it is polling and ready
                try:
                    if (await c.get("/ready")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("the worker never became ready")
            h = await env.client.start_workflow(
                "ExtractionWorkflow",
                make_input(kind="extraction", outline_gate=False),
                id="drain-1",
                task_queue="llm-extraction",
            )
            while db.count("run_extraction") == 0:  # the activity is now running
                await asyncio.sleep(0.05)
            os.kill(os.getpid(), signal.SIGTERM)  # the stop signal, as Kubernetes sends it
            for _ in range(100):  # /ready goes 503 at once, while the activity is still in flight
                if (await c.get("/ready")).status_code == 503:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("/ready never went 503 during the drain")
            assert "activity_finished" not in events  # still draining
            assert (
                await c.get("/live")
            ).status_code == 200  # the ops server answers during the drain
            assert (await c.get("/metrics")).status_code == 200
        await asyncio.wait_for(task, 30)  # run() returned: no SystemExit, no re-raised signal
        # the drain let the in-flight activity finish and record its result. The workflow itself carries on
        # on another pod (this worker stopped polling), so it is still running, not failed.
        history = await h.fetch_history()
        done = [e for e in history.events if e.HasField("activity_task_completed_event_attributes")]
        failed = [e for e in history.events if e.HasField("activity_task_failed_event_attributes")]
        assert len(done) >= 1 and not failed
        assert (await h.describe()).status.name == "RUNNING"
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        await env.shutdown()
    assert events.index("polling") < events.index("draining") < events.index("activity_finished")
    assert events.index("activity_finished") < events.index("drained") < events.index("ops_stopped")
    assert (
        events.index("ops_stopped")
        < events.index("engine_closed_called")
        < events.index("engine_closed")
    )
    print("SHUTDOWN ORDER:", events)  # noqa: T201


async def test_connect_uses_the_encrypting_converter(tmp_path) -> None:  # noqa: ANN001
    key = tmp_path / "key"
    key.write_bytes(b"k" * 32)
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        cfg = env.client.service_client.config
        settings = Settings(
            worker_kind="generation",
            worker_tier="standard",
            temporal_address=cfg.target_host,
            payload_codec_key_id="k1",
            payload_codec_key_path=str(key),
        )
        client = await worker_mod.connect(settings)
        assert type(client.data_converter.payload_codec).__name__ == "AesGcmPayloadCodec"
    finally:
        await env.shutdown()


def test_tls_is_off_without_files_and_loads_both_when_set(tmp_path) -> None:  # noqa: ANN001
    assert worker_mod._tls(Settings(worker_kind="generation", worker_tier="standard")) is False  # noqa: SLF001
    cert, key = tmp_path / "c", tmp_path / "k"
    cert.write_bytes(b"CERT")
    key.write_bytes(b"KEY")
    tls = worker_mod._tls(  # noqa: SLF001
        Settings(
            worker_kind="generation",
            worker_tier="standard",
            temporal_tls_cert_path=str(cert),
            temporal_tls_key_path=str(key),
        )
    )
    assert tls.client_cert == b"CERT" and tls.client_private_key == b"KEY"


def test_runtime_and_interceptors() -> None:
    off = Settings(worker_kind="generation", worker_tier="standard", enable_metrics=False)
    assert build_runtime(off) is not None and build_interceptors(off) == []
    traced = Settings(
        worker_kind="generation",
        worker_tier="standard",
        otel_exporter_otlp_endpoint="http://otel:4317",
    )
    assert len(build_interceptors(traced)) == 1


async def test_operator_commands() -> None:
    env = (
        await WorkflowEnvironment.start_local()
    )  # the time-skipping server has no DescribeTaskQueue
    try:
        op = Operator("alice", "TICKET-1")
        rows, audit = await list_live_runs(env.client, op, tenant_id="t-1", kind="document")
        assert (
            rows == [] and audit.command == "list_live_runs" and audit.operator == op
        )  # no visibility: falls back
        with pytest.raises(ValueError):
            await list_live_runs(env.client, op, tenant_id="x' OR 1=1 --")
        counts, audit = await drain_report(env.client, op, "llm-generation")
        assert (
            set(counts) == {"workflow_pollers", "activity_pollers"}
            and audit.command == "drain_report"
        )
    finally:
        await env.shutdown()


async def test_replay_directory_replays_committed_histories(tmp_path) -> None:  # noqa: ANN001
    from harness import stack
    from llm_workers_generation.versioning.replay import replay_directory

    async with stack(FakeDb(), queue="llm-extraction") as s:
        h = await s.client.start_workflow(
            "ExtractionWorkflow",
            make_input(kind="extraction", outline_gate=False),
            id="rep-1",
            task_queue="llm-extraction",
        )
        await h.result()
        (tmp_path / "rep-1.json").write_text((await h.fetch_history()).to_json())
    assert await replay_directory(tmp_path) == 1
