"""Error translation (LLD §4.4, WS-11) and the activities against the fake engine (§7)."""

from uuid import UUID

import fake_engine as fe
import pytest
from fake_engine import CANARY, FakeDb, FakeEngineApi, make_input
from llm_models.workers import SectionJob
from llm_workers_generation import errors
from llm_workers_generation.activities.generation import GenerationActivities
from llm_workers_generation.types import (
    FactsRequest,
    FailRequest,
    OutlineRequest,
    RunRequest,
    StageRequest,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


def acts(db: FakeDb) -> GenerationActivities:
    return GenerationActivities(FakeEngineApi(db))


async def run(fn, arg):  # noqa: ANN001, ANN201
    return await ActivityEnvironment().run(fn, arg)


def section_job(db: FakeDb, ref: str, raw: dict) -> SectionJob:  # noqa: ANN001
    return SectionJob(next(s["id"] for s in db.sections if s["ref"] == ref), "stage2", raw)


async def prepared(**kw):  # noqa: ANN003, ANN201
    db, raw = FakeDb(status="generating", **kw), make_input(outline_gate=False)
    db.add_sections([("1", "intro", True), ("2", "scope", False)])
    return db, raw


# -- error translation ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raised", "code", "non_retryable"),
    [
        (fe.ScopeRevoked, errors.ERR_SCOPE_REVOKED, True),
        (fe.ResidencyRefused, errors.ERR_RESIDENCY, True),
        (fe.SchemaViolation, errors.ERR_SCHEMA, True),
        (fe.NothingToMint, errors.ERR_NOTHING_TO_MINT, True),
        (fe.InferenceUnavailable, errors.ERR_INFERENCE, False),
        (RuntimeError, errors.ERR_INTERNAL, False),
    ],
)
async def test_errors_become_fixed_content_free_application_errors(
    raised, code, non_retryable
) -> None:  # noqa: ANN001
    db, raw = await prepared()
    api = FakeEngineApi(db)

    async def boom(ctx):  # noqa: ANN001, ANN202
        raise raised(f"secret {CANARY}") if raised is RuntimeError else raised()

    api.run_state = boom  # type: ignore[method-assign]
    with pytest.raises(ApplicationError) as info:
        await run(GenerationActivities(api).run_state, RunRequest(raw))
    err = info.value
    assert err.type == code and err.non_retryable is non_retryable
    assert err.message == errors.FIXED_MESSAGES[code] and CANARY not in err.message
    assert not err.details and err.__cause__ is None  # no details, no chaining (WS-11)


async def test_run_gone_and_terminal_are_results_not_failures() -> None:
    db, raw = await prepared()
    db.gone = True
    assert (await run(acts(db).run_state, RunRequest(raw))).gone
    assert (await run(acts(db).begin_stage, StageRequest(raw, "stage1"))).gone
    db2, raw2 = await prepared()
    db2.status = "cancelled"
    res = await run(acts(db2).begin_stage, StageRequest(raw2, "stage1"))
    assert res.skipped_terminal and not res.ok


async def test_quota_is_a_result_in_every_model_calling_activity() -> None:
    db, raw = await prepared(behaviors={"facts": ["quota"], "coherence": ["quota"], "1": ["quota"]})
    db.sections[0].update(state="complete", version=True)
    db.sections[1].update(state="complete", version=True)
    assert (
        await run(acts(db).capture_facts, FactsRequest(raw, (db.sections[0]["id"],)))
    ).quota_suspended
    assert (await run(acts(db).coherence_pass, RunRequest(raw))).quota_suspended
    db.sections[0].update(state="pending", version=False)
    out = await run(acts(db).write_section, section_job(db, "1", raw))
    assert out.quota_suspended and not out.skipped_terminal


# -- write_section -------------------------------------------------------------------------------
async def test_write_section_returns_identifiers_only() -> None:
    db, raw = await prepared()
    out = await run(acts(db).write_section, section_job(db, "2", raw))
    assert out.state == "complete" and out.version_id
    assert CANARY not in repr(out)  # WA-4: no text in a result
    assert CANARY in db.content["2"]  # the text went to the engine, not into the result
    assert db.count("record_checkpoint") == 1


async def test_write_section_is_idempotent_for_a_section_this_run_wrote() -> None:
    db, raw = await prepared()
    job = section_job(db, "2", raw)
    await run(acts(db).write_section, job)
    calls = db.count("call_model:2")
    again = await run(acts(db).write_section, job)
    assert (
        again.state == "complete" and db.count("call_model:2") == calls
    )  # repeat costs nothing (WA-5)


async def test_write_section_never_writes_a_terminal_run() -> None:
    db, raw = await prepared()
    db.status = "cancelled"
    out = await run(acts(db).write_section, section_job(db, "2", raw))
    assert out.skipped_terminal and db.count("save_section") == 0


async def test_write_section_scope_is_reresolved_first() -> None:
    db, raw = await prepared()
    await run(acts(db).write_section, section_job(db, "2", raw))
    assert db.count("reresolve") >= 1 and db.count("retrieve") >= 1


async def test_a_gap_is_saved_when_evidence_stays_insufficient() -> None:
    db, raw = await prepared(behaviors={"retrieve:scope": ["insufficient", "insufficient"]})
    out = await run(acts(db).write_section, section_job(db, "2", raw))
    assert out.state == "gap" and out.gap_reason == "no_evidence"


async def test_unknown_section_fails_content_free() -> None:
    db, raw = await prepared()
    out = await run(acts(db).write_section, SectionJob(str(UUID(int=9)), "stage2", raw))
    assert out.state == "failed" and out.failure_reason == "internal"


# -- orchestration -------------------------------------------------------------------------------
async def test_outline_from_pack_gated_and_revised() -> None:
    db, raw = FakeDb(), make_input()
    step = await run(acts(db).outline, OutlineRequest(raw))
    assert (
        step.saved and len(step.saved.section_ids) == 3 and db.status == "awaiting_outline_approval"
    )
    db.outline_state = "revising"
    step = await run(acts(db).outline, OutlineRequest(raw, (("1", "intro"), ("7", "price"))))
    assert [s["ref"] for s in db.sections] == ["1", "7"] and db.sections[0]["foundation"] is True


async def test_outline_regeneration_is_never_gated() -> None:
    db, raw = FakeDb(), make_input(origin="regeneration", outline_gate=False)
    step = await run(acts(db).outline, OutlineRequest(raw))
    assert step.saved and db.status == "pending"


async def test_capture_facts_rejects_a_malformed_answer_without_echoing_it() -> None:
    db, raw = await prepared()
    db.sections[0].update(state="complete", version=True)
    api = FakeEngineApi(db)

    async def bad(*a, **k):  # noqa: ANN002, ANN003, ANN202
        return type("A", (), {"text": f"not json {CANARY}"})()

    api.call_model = bad  # type: ignore[method-assign]
    with pytest.raises(ApplicationError) as info:
        await run(
            GenerationActivities(api).capture_facts, FactsRequest(raw, (db.sections[0]["id"],))
        )
    assert info.value.type == errors.ERR_SCHEMA and CANARY not in str(info.value)


async def test_coherence_surfaces_a_conflict_once() -> None:
    db, raw = await prepared(conflict_value="999")
    for s in db.sections:
        s.update(state="complete", version=True)
    step = await run(acts(db).coherence_pass, RunRequest(raw))
    assert step.verdict == "conflicted" and step.conflict_count == 1
    assert db.count("call_model:coherence") == 1  # not retried (WP-16)


async def test_fail_run_is_guarded_and_reports_whether_it_ended_the_run() -> None:
    db, raw = await prepared()
    first = await run(acts(db).fail_run, FailRequest(raw, "internal", "stage1"))
    second = await run(acts(db).fail_run, FailRequest(raw, "internal", "stage1"))
    assert first.ok and not second.ok and db.events.count("GenerationFailed") == 1  # WEV-5


async def test_heartbeats_beat_in_the_background_with_ids_only() -> None:
    import asyncio

    from llm_workers_generation.activities.heartbeat import heartbeating

    beats: list[tuple] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *d: beats.append(d)

    async def long_call() -> None:
        async with heartbeating("sec-1", "stage2", interval=0.01):
            await asyncio.sleep(0.12)

    await env.run(long_call)
    assert len(beats) >= 3 and all(b == ("sec-1", "stage2") for b in beats)
