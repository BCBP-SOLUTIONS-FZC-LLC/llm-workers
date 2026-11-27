"""Fixes from the pre-merge review: each one is a way a run could be lost or left open."""

import asyncio
from datetime import timedelta

from fake_engine import FakeDb
from harness import stack
from llm_workers_generation import tuning
from llm_workers_generation.activities.slots import SlotActivities
from temporalio import activity
from temporalio.exceptions import ApplicationError


async def test_a_store_outage_on_the_state_read_does_not_fail_the_run() -> None:
    """``run_state`` is retried for a window (an hour), not three times: an outage is not a failed run."""
    async with stack(FakeDb()) as s:
        real, calls = s.api.run_state, {"n": 0}

        async def flaky(ctx):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] <= 8:  # more than the old three attempts
                raise RuntimeError("database unreachable")
            return await real(ctx)

        s.api.run_state = flaky  # type: ignore[method-assign]
        handle, _ = await s.start(outline_gate=False)
        assert (await handle.result())["status"] == "complete"
        assert calls["n"] > 8


async def test_a_state_read_that_stays_down_past_the_window_fails_the_run_recorded() -> None:
    async with stack(FakeDb()) as s:

        async def down(ctx):  # noqa: ANN001, ANN202
            raise RuntimeError("database unreachable")

        s.api.run_state = down  # type: ignore[method-assign]
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()  # an hour of retries, skipped by the test server
        assert result["status"] == "failed" and result["failure_reason"] == "internal"
        assert s.db.status == "failed" and s.db.count("fail_run") == 1


async def test_the_gate_does_not_spin_on_the_row_once_its_deadline_has_passed() -> None:
    """Deadline passed, row ``outline_building`` at the same version: one read per pause, not a loop."""
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        s.db.status = "outline_building"  # the API acted, the outline is being rebuilt
        s.db.gate_expires_at = "2000-01-01T00:00:00+00:00"  # and the deadline is long past
        await s.env.sleep(timedelta(seconds=tuning.WAIT_RECONCILE_SECONDS + 5))  # one reconcile
        before = s.db.count("run_state")
        await s.env.sleep(timedelta(seconds=60))
        reads = s.db.count("run_state") - before
        assert reads <= 60 / tuning.GATE_RECHECK_SECONDS + 3, reads  # about twelve, not thousands
        s.db.status = "awaiting_outline_approval"  # the row settles: the expired gate now fails
        result = await asyncio.wait_for(handle.result(), 60)
        assert result["failure_reason"] == "gate_expired"


async def test_a_slot_request_that_fails_closes_the_section_failed(monkeypatch) -> None:  # noqa: ANN001
    @activity.defn(name="acquire_slot")
    async def refused(self, req):  # noqa: ANN001, ANN202
        raise ApplicationError("no slot", type="Internal", non_retryable=True)

    monkeypatch.setattr(SlotActivities, "acquire_slot", refused)
    async with stack(FakeDb()) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()  # the foundation section cannot get a slot: the run fails
        assert result["status"] == "failed" and result["failure_reason"] == "internal"
        assert s.db.count("fail_section") >= 1  # the section row was closed, not left open
        assert [x["state"] for x in s.db.sections if x["foundation"]] == ["failed"]


async def test_a_step_refused_on_a_row_that_is_not_terminal_fails_the_run_recorded() -> None:
    async with stack(FakeDb()) as s:

        async def refuse(ctx, stage):  # noqa: ANN001, ANN202
            return False  # the engine says no, and the row is not terminal

        s.api.begin_stage = refuse  # type: ignore[method-assign]
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "internal"
        assert s.db.status == "failed" and s.db.count("fail_run") == 1  # recorded, not left open
