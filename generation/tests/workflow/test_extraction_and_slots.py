"""ExtractionWorkflow and TenantSlots (LLD §6.3, §12.2)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

from fake_engine import FakeDb
from harness import slots, stack
from llm_workers_generation import tuning
from llm_workers_generation.types import SlotRequest
from llm_workers_generation.workflows import TenantSlots


async def test_extraction_completes() -> None:
    db = FakeDb()
    async with stack(db, queue="llm-extraction") as s:
        raw_handle = await s.client.start_workflow(
            "ExtractionWorkflow",
            __import__("fake_engine").make_input(kind="extraction", outline_gate=False),
            id=str(uuid4()),
            task_queue="llm-extraction",
        )
        assert (await raw_handle.result())["status"] == "complete"
        assert db.count("run_extraction") == 1 and db.stage == "extract"


async def test_extraction_quota_suspends_then_resumes() -> None:
    db = FakeDb(behaviors={"extract": ["quota"]})
    async with stack(db, queue="llm-extraction") as s:
        h = await s.client.start_workflow(
            "ExtractionWorkflow",
            __import__("fake_engine").make_input(kind="extraction", outline_gate=False),
            id=str(uuid4()),
            task_queue="llm-extraction",
        )
        await s.wait_status("suspended")
        await s.resume(h)
        assert (await h.result())["status"] == "complete"
        assert db.count("run_extraction") == 2


async def test_extraction_schema_violation_and_residency_fail_the_run() -> None:
    for action, reason in (("schema", "schema_violation"), ("residency", "residency_capacity")):
        db = FakeDb(behaviors={"extract": [action]})
        async with stack(db, queue="llm-extraction") as s:
            h = await s.client.start_workflow(
                "ExtractionWorkflow",
                __import__("fake_engine").make_input(kind="extraction", outline_gate=False),
                id=str(uuid4()),
                task_queue="llm-extraction",
            )
            result = await h.result()
            assert result["status"] == "failed" and result["failure_reason"] == reason
            assert db.count("run_extraction") == 1


async def test_extraction_restart_runs_again_from_the_row() -> None:
    db = FakeDb(status="generating", stage="extract")
    async with stack(db, queue="llm-extraction") as s:
        h = await s.client.start_workflow(
            "ExtractionWorkflow",
            __import__("fake_engine").make_input(kind="extraction", outline_gate=False),
            id=str(uuid4()),
            task_queue="llm-extraction",
        )
        assert (await h.result())["status"] == "complete"


async def _slots(s, tenant: str, limit: int = 2, lease: int = 100):  # noqa: ANN001, ANN202
    return await slots(s, tenant, limit, lease)


async def test_tenant_slots_are_fifo_and_bounded() -> None:
    async with stack() as s:
        handle, acquire = await _slots(s, "t1", limit=2)
        h1, h2, h3 = await acquire("a"), await acquire("b"), await acquire("c")
        assert (await h1.result()).granted and (await h2.result()).granted
        # the third waits: the tenant's limit is two. Each ask ends after ACQUIRE_POLL_SECONDS with
        # "not granted" (nothing is left waiting inside the entity) and the request keeps its place
        assert not (await asyncio.wait_for(h3.result(), 30)).granted
        await handle.execute_update(TenantSlots.release, SlotRequest("a"))
        again = await acquire("c")  # the same request id asks again
        assert (await asyncio.wait_for(again.result(), 30)).granted
        await handle.terminate()


async def test_a_waiting_request_that_asks_again_leaves_no_open_handler_behind() -> None:
    async with stack() as s:
        handle, acquire = await _slots(s, "t1b", limit=1)
        assert (await (await acquire("a")).result()).granted
        for _ in range(4):  # four polls of the same request: each is answered, none stays open
            assert not (await asyncio.wait_for((await acquire("b")).result(), 30)).granted
        # the entity can finish: all handlers have returned, so a release is answered at once
        assert await asyncio.wait_for(
            handle.execute_update(TenantSlots.release, SlotRequest("a")), 30
        )
        assert (await asyncio.wait_for((await acquire("b")).result(), 30)).granted  # still queued
        await handle.terminate()


async def test_waiting_request_can_be_withdrawn() -> None:
    async with stack() as s:
        handle, acquire = await _slots(s, "t2", limit=1)
        h1, h2 = await acquire("a"), await acquire("b")
        assert (await h1.result()).granted
        assert await handle.execute_update(TenantSlots.release, SlotRequest("b"))  # withdraw
        assert not (await h2.result()).granted
        await handle.terminate()


async def test_expired_lease_is_reaped() -> None:
    async with stack() as s:
        handle, acquire = await _slots(s, "t3", limit=1, lease=60)
        h1, h2 = await acquire("a"), await acquire("b")
        assert (await h1.result()).granted
        assert not (await asyncio.wait_for(h2.result(), 30)).granted  # b waits, its ask ends
        await s.env.sleep(timedelta(seconds=65))  # a's 60 s lease expires and the reaper frees it
        assert (await asyncio.wait_for((await acquire("b")).result(), 30)).granted
        await handle.terminate()


async def test_invalid_request_is_rejected_by_the_validator() -> None:
    import pytest
    from temporalio.client import WorkflowUpdateFailedError

    async with stack() as s:
        handle, acquire = await _slots(s, "t4")
        await acquire("ok")
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(TenantSlots.acquire, SlotRequest(""))
        await handle.terminate()


async def test_per_tenant_bound_holds_across_documents() -> None:
    db = FakeDb(call_delay=0.05, skeleton=[(str(i), "scope", False) for i in range(8)])
    async with stack(db) as s:
        handles = [(await s.start(outline_gate=False))[0] for _ in range(4)]
        # the fake keeps one run: every workflow shares it, which is enough to measure concurrency
        await asyncio.gather(*(h.result() for h in handles))
        assert tuning.FANOUT_MAX_PER_DOCUMENT < db.peak <= tuning.FANOUT_MAX_PER_TENANT
