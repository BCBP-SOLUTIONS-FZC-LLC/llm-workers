"""The 30-day suspension timeout on the real clock (LLD §9.1 step 5), without the time-skipping server.

``SUSPEND_MAX_DAYS`` is patched to ten seconds, so the whole path runs on a dev server in real time:
a quota refusal suspends the run, a ``resume`` before the deadline continues it, and with no resume
the failure is recorded only when the deadline passes, from ``suspended`` (engine 1.1.0's edge).
The deadline is the row's clock (``suspended_at``), as in production (G-3).
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fake_engine import FakeDb
from harness import local_stack
from llm_workers_generation import tuning

DEADLINE_SECONDS = 10


@pytest.fixture(autouse=True)
def _short_deadline(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(tuning, "SUSPEND_MAX_DAYS", DEADLINE_SECONDS / 86400)


async def test_resume_before_the_deadline_continues_the_run_and_nothing_is_failed() -> None:
    db = FakeDb(behaviors={"2": ["quota"]})
    async with local_stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        await asyncio.sleep(2)  # well inside the deadline: still suspended, nothing recorded
        assert db.status == "suspended" and db.count("fail_run") == 0
        await s.resume(handle)
        result = await asyncio.wait_for(handle.result(), 60)
    assert result["status"] == "complete"
    assert db.count("fail_run") == 0 and db.failed_from is None
    assert db.count("save_section") == 3  # the sections that were refused ran after the resume


async def test_without_a_resume_the_failure_is_recorded_only_at_the_deadline() -> None:
    db = FakeDb(behaviors={"2": ["quota"]})
    async with local_stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        stamp = datetime.fromisoformat(db.suspended_at or "")
        await asyncio.sleep(DEADLINE_SECONDS * 0.6)  # past half way, before the deadline
        assert db.status == "suspended" and db.count("fail_run") == 0  # nothing recorded early
        result = await asyncio.wait_for(handle.result(), 60)
        recorded_at = datetime.now(UTC)
    assert result["status"] == "failed" and result["failure_reason"] == "quota_exhausted"
    assert db.failed_from == "suspended" and db.status == "failed"
    assert "GenerationFailed" in db.events and db.count("fail_run") == 1
    assert recorded_at - stamp >= timedelta(seconds=DEADLINE_SECONDS - 1)  # not before the deadline
