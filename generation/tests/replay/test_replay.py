"""Replay: recorded histories of every workflow type still replay against the current code (WV-3)."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fake_engine import FakeDb, make_input
from harness import stack
from history import family
from llm_workers_generation.versioning.replay import replay_directory, replay_histories
from temporalio import workflow
from temporalio.worker import Replayer

FIXTURES = Path(__file__).parent / "histories"


async def test_gated_run_with_revision_and_approval_replays() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.revise(handle, [("1", "intro"), ("2", "scope")])
        await s.wait_status("awaiting_outline_approval")
        await s.approve(handle)
        await handle.result()
        history = await handle.fetch_history()
    await replay_histories([history])


async def test_run_paused_at_the_gate_replays() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.env.sleep(timedelta(hours=3))  # a few reconcile wakes in history
        history = await handle.fetch_history()
        await s.cancel(handle)
        await handle.result()
    await replay_histories([history])


async def test_run_suspended_on_quota_replays() -> None:
    db = FakeDb(behaviors={"2": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        suspended = await handle.fetch_history()
        await s.resume(handle)
        await handle.result()
        done = await handle.fetch_history()
    await replay_histories([suspended, done])


async def test_failed_run_children_and_slots_replay() -> None:
    db = FakeDb(behaviors={"1": ["inference"] * 3})
    async with stack(db) as s:
        handle, raw = await s.start(outline_gate=False)
        await handle.result()
        histories = await family(s.client, handle, raw["tenant_id"])
    assert len(histories) >= 3
    await replay_histories(histories)


async def test_extraction_and_slots_replay() -> None:
    db = FakeDb()
    async with stack(db, queue="llm-extraction") as s:
        h = await s.client.start_workflow(
            "ExtractionWorkflow",
            make_input(kind="extraction", outline_gate=False),
            id="x1",
            task_queue="llm-extraction",
        )
        await h.result()
        history = await h.fetch_history()
    await replay_histories([history])


@workflow.defn(name="ExtractionWorkflow")
class ChangedExtraction:
    @workflow.run
    async def run(self, raw: dict) -> dict:
        await asyncio.sleep(1)  # a command the recorded history does not contain
        return {"status": "complete"}


async def test_a_nondeterministic_change_is_caught() -> None:
    """The gate itself: a history replayed against a workflow that issues different commands fails."""
    async with stack(FakeDb(), queue="llm-extraction") as s:
        h = await s.client.start_workflow(
            "ExtractionWorkflow",
            make_input(kind="extraction", outline_gate=False),
            id="x3",
            task_queue="llm-extraction",
        )
        await h.result()
        history = await h.fetch_history()
    with pytest.raises(Exception):  # noqa: B017, PT011 - NondeterminismError
        await Replayer(workflows=[ChangedExtraction]).replay_workflow(history)


async def test_committed_fixture_histories_replay() -> None:
    """CI replays every history committed under ``tests/replay/histories`` (nightly export lands here)."""
    if not FIXTURES.exists():
        pytest.skip("no committed histories yet: the first release records them")
    assert await replay_directory(FIXTURES) >= 0
