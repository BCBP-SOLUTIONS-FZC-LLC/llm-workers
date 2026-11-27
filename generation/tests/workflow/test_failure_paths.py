"""A terminal write that cannot be recorded (LLD §13.2) and the run deadline (WW-3)."""

import pytest
from fake_engine import FakeDb
from harness import stack
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError


async def test_unrecorded_failure_is_retried_then_fails_the_workflow_visibly() -> None:
    db = FakeDb(behaviors={"1": ["inference"] * 3})
    async with stack(db) as s:

        async def down(ctx, reason, stage):  # noqa: ANN001, ANN202
            db.tick("fail_run_attempt")
            raise RuntimeError("database unreachable")

        s.api.fail_run = down  # type: ignore[method-assign]
        handle, _ = await s.start(outline_gate=False)
        with pytest.raises(WorkflowFailureError) as info:
            await handle.result()
        cause = info.value.cause
        assert isinstance(cause, ApplicationError) and cause.type == "UnrecordedFailure"
        assert db.count("fail_run_attempt") > 3  # retried across the 60-minute window, not once
        assert (
            db.status != "failed"
        )  # the row was never written: the alert and W-8 are the recovery


async def test_run_deadline_fails_the_run_and_stops_spending_tokens(monkeypatch) -> None:  # noqa: ANN001
    from llm_workers_generation import tuning

    monkeypatch.setattr(tuning, "RUN_MAX_SECONDS", -1)  # already past the deadline
    db = FakeDb(call_delay=0.0)
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "internal"


async def test_suspension_timeout_records_a_failure_from_suspended() -> None:
    """Engine 1.1.0 adds ``suspended -> failed`` (ADR-0003 gap 5): the 30-day timeout is recorded.

    Under engine 1.0.0 this path ended ``UnrecordedFailure`` and left the run ``suspended``. The fake's
    ``fail_run`` now checks the real engine's ``RUN_TRANSITIONS``, so the write only succeeds because
    the edge exists.
    """
    db = FakeDb(behaviors={"2": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()  # no resume: 30 days pass
        assert result["status"] == "failed" and result["failure_reason"] == "quota_exhausted"
        assert db.failed_from == "suspended"  # the write came from the suspended state
        assert db.status == "failed" and db.failure_reason == "quota_exhausted"
        assert "GenerationFailed" in db.events and db.count("fail_run") == 1


async def test_failure_before_the_outline_is_recorded_from_pending() -> None:
    """An ungated run that fails before its first stage began is still ``pending`` (engine 1.1.0)."""
    db = FakeDb(behaviors={"scope": ["scope"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "scope_revoked"
        assert db.failed_from == "pending"
        assert db.status == "failed" and "GenerationFailed" in db.events


async def test_failure_while_the_outline_is_built_is_recorded_from_outline_building() -> None:
    """A gated run begins the ``outline`` stage first, so a failure in it comes from ``outline_building``."""
    db = FakeDb(behaviors={"scope": ["scope"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=True)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "scope_revoked"
        assert db.failed_from == "outline_building"
        assert db.status == "failed" and "GenerationFailed" in db.events


async def test_a_blip_reading_a_sections_text_is_retried_and_the_run_completes() -> None:
    """``load_section_text`` raises ``ContentUnavailable`` for ANY read failure, a transient one included.

    It is retryable (ADR-0003): the bounded attempts of ``capture_facts`` ride out one blip.
    """
    db = FakeDb(behaviors={"content": ["content"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        assert (await handle.result())["status"] == "complete"
        assert db.count("section_text") >= 2 and db.count("fail_run") == 0


async def test_a_persistent_read_failure_ends_as_a_recorded_internal_failure() -> None:
    """A fault that outlasts the attempts (3) is recorded, never left to ``UnrecordedFailure``."""
    db = FakeDb(behaviors={"content": ["content"] * 3})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "internal"
        assert db.status == "failed" and "GenerationFailed" in db.events
        assert db.count("section_text") == 3  # the activity's bounded attempts, no more
