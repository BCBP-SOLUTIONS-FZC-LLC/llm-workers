"""A section child cancelled just as its last ``write_section`` retry fails (LLD §13.5, WB-6).

Real clock, real dev server, the worker versioning ``worker.py`` sets up (``local_stack``). The
time-skipping server is not used: it skips the retry backoff, so the last failure and the cancel
meet by construction and the server rejects the cancel command (``ACTIVITY_UNKNOWN``), which leaves
the child with a started workflow task that is never completed.
"""

import asyncio
from typing import Any

from fake_engine import FakeDb
from harness import local_stack
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus

CLOSED = (WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.CANCELED)


async def test_cancel_as_the_last_retry_fails_ends_the_run_failed_and_releases_the_slot() -> None:
    db = FakeDb(
        skeleton=[("2", "scope", False), ("3", "price", False)],
        behaviors={"2": ["inference"] * 3, "3": ["inference"] * 3},
    )
    async with local_stack(db) as s:
        reached, release, attempts = asyncio.Event(), asyncio.Event(), 0
        real: Any = s.api.call_model

        async def held(ctx, task, prompt, *, section_ref, attempt, deadline_seconds):  # noqa: ANN001, ANN202
            nonlocal attempts
            if section_ref == "2":
                attempts += 1
                if attempts == 3:  # the last attempt: hold it until the cancel is sent
                    reached.set()
                    await release.wait()
            return await real(
                ctx,
                task,
                prompt,
                section_ref=section_ref,
                attempt=attempt,
                deadline_seconds=deadline_seconds,
            )

        s.api.call_model = held  # type: ignore[method-assign]
        handle, _ = await s.start(outline_gate=False)
        await asyncio.wait_for(reached.wait(), 60)
        section_id = next(x["id"] for x in db.sections if x["ref"] == "2")
        child = s.client.get_workflow_handle(f"{handle.id}:s:{section_id}:stage2")
        release.set()  # attempt 3 fails now...
        await child.cancel()  # ...and the cancel arrives with it

        result = await asyncio.wait_for(handle.result(), 90)  # a hard bound: no hang
        assert result["status"] == "failed" and result["failure_reason"] == "internal"
        assert db.status == "failed" and db.count("fail_run") == 1

        assert (await child.describe()).status in CLOSED  # the child ended, it is not left running
        scheduled: dict[int, str] = {}
        completed: set[int] = set()
        async for e in child.fetch_history_events():
            if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                a = e.activity_task_scheduled_event_attributes
                scheduled[e.event_id] = a.activity_type.name
            elif e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                completed.add(e.activity_task_completed_event_attributes.scheduled_event_id)
        assert any(scheduled[i] == "release_slot" for i in completed)  # the slot was released
