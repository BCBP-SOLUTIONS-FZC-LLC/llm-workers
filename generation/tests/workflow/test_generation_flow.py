"""GenerationWorkflow end to end on the time-skipping server (LLD §18.3)."""

import asyncio
from datetime import UTC, datetime, timedelta

from fake_engine import FakeDb
from harness import local_stack, slots, stack
from temporalio.client import WorkflowFailureError  # noqa: F401


async def test_ungated_document_completes() -> None:
    async with stack() as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "complete"
        assert s.db.status == "complete"
        assert [x["state"] for x in s.db.sections] == ["complete"] * 3
        assert s.db.facts and s.db.coherence == "consistent" and s.db.minted
        assert s.db.events[-1] == "GenerationCompleted"
        assert s.db.count("fail_run") == 0
        assert s.db.checkpoints and all(isinstance(n, str) for n, _, _ in s.db.checkpoints)


async def test_gate_approval() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        assert all(x["state"] == "pending" for x in s.db.sections)
        await s.approve(handle)
        assert (await handle.result())["status"] == "complete"
        assert s.db.events.count("OutlineReady") == 1


async def test_gate_revision_then_approval() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.revise(handle, [("1", "intro"), ("9", "scope")])
        await s.wait_status("awaiting_outline_approval")
        assert s.db.outline_version == 2
        assert [x["ref"] for x in s.db.sections] == ["1", "9"]
        await s.approve(handle)
        assert (await handle.result())["status"] == "complete"
        assert s.db.events.count("OutlineReady") == 2


async def test_gate_expiry_fails_run() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        result = await handle.result()  # nobody approves: 30 days pass in milliseconds
        assert result["status"] == "failed" and result["failure_reason"] == "gate_expired"
        assert s.db.failure_reason == "gate_expired"


async def test_cancel_while_waiting_writes_nothing() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.cancel(handle)
        assert (await handle.result())["status"] == "cancelled"
        assert (
            s.db.status == "cancelled"
            and s.db.count("fail_run") == 0
            and s.db.count("complete_run") == 0
        )


async def test_lost_cancel_ends_when_row_reads_terminal() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.cancel(handle, signal=False)  # the signal never arrives
        assert (await handle.result())[
            "status"
        ] == "cancelled"  # the reconcile reads the row (WG-4)
        assert s.db.count("fail_run") == 0


async def test_duplicate_and_stale_signals_are_ignored() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await handle.signal(
            "approve_outline", {"outline_version": 0}
        )  # stale: below the current version
        await handle.signal("resume")  # the run is not suspended: ignored (WG-6)
        await handle.signal(
            "revise_outline", {"outline_version": 1, "sections": []}
        )  # stale (WG-2)
        await asyncio.sleep(0.3)
        assert (
            s.db.status == "awaiting_outline_approval" and s.db.count("save_outline") == 1
        )  # none acted
        await s.approve(handle)
        await handle.signal("approve_outline", {"outline_version": 1})  # duplicate approve (WG-1)
        result = await handle.result()
        assert result["status"] == "complete"
        assert (
            s.db.count("save_outline") == 1
            and s.db.count("complete_run") == 1
            and s.db.count("fail_run") == 0
        )


async def test_cancel_during_stage2_writes_nothing_onto_the_run_and_frees_the_slots() -> None:
    """LLD §18.7 "cancel during stage 2", with a duplicate cancel (WG-3)."""
    skeleton = [("0", "intro", True)] + [(str(i), "scope", False) for i in range(1, 9)]
    db = FakeDb(skeleton=skeleton, call_delay=0.4)
    async with stack(db) as s:
        handle, raw = await s.start(outline_gate=False)
        async with asyncio.timeout(20):
            while not (db.stage == "stage2" and db.active >= 2):
                await asyncio.sleep(0.02)
        await s.cancel(handle)  # the API wrote `cancelled`, then signals
        await handle.signal("cancel", {"reason": "duplicate"})  # a duplicate is a no-op
        assert (await handle.result())["status"] == "cancelled"
        saved = db.count("save_section")
        await asyncio.sleep(1.2)  # in-flight activities finish their model calls...
        assert (
            db.count("save_section") == saved
        )  # ...and write nothing onto the cancelled run (WB-6)
        assert (
            db.count("fail_section") == 0
            and db.count("fail_run") == 0
            and db.count("complete_run") == 0
        )
        assert db.status == "cancelled"
        # every slot the cancelled sections held was released: ten fresh acquires are all granted
        _, acquire = await slots(s, raw["tenant_id"])
        handles = [await acquire(f"fresh-{i}") for i in range(10)]
        for h in handles:
            assert (await asyncio.wait_for(h.result(), 10)).granted


async def test_lost_approve_is_recovered_by_reconcile() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.approve(handle, signal=False)  # W-3 committed, the signal was lost
        assert (await handle.result())["status"] == "complete"


async def test_lost_revise_is_recovered_by_reconcile() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.revise(handle, [("1", "intro")], signal=False)
        await s.env.sleep(timedelta(hours=1, minutes=5))  # one reconcile interval passes
        await s.wait_status(
            "awaiting_outline_approval"
        )  # looped to the outline phase on the reconcile
        assert s.db.outline_version == 2
        await s.approve(handle)
        assert (await handle.result())["status"] == "complete"


async def test_quota_suspends_in_section_phase_and_resume_signal_continues() -> None:
    db = FakeDb(behaviors={"2": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        assert s.db.failure_reason == "quota_exhausted" and s.db.suspended_at
        await s.resume(handle)
        assert (await handle.result())["status"] == "complete"
        assert s.db.count("suspend_for_quota") == 1
        assert s.db.count("save_section") == 3  # finished sections were not written twice


async def test_quota_resume_with_lost_signal_is_recovered() -> None:
    db = FakeDb(behaviors={"2": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        await s.resume(handle, signal=False)
        assert (await handle.result())["status"] == "complete"


async def test_quota_in_facts_phase_resumes_that_phase() -> None:
    db = FakeDb(behaviors={"facts": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        await s.resume(handle)
        assert (await handle.result())["status"] == "complete"
        assert s.db.facts and s.db.count("call_model:facts") == 2


async def test_quota_in_coherence_phase_resumes_that_phase() -> None:
    db = FakeDb(behaviors={"coherence": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        await s.wait_status("suspended")
        await s.resume(handle)
        assert (await handle.result())["status"] == "complete"
        assert s.db.count("call_model:coherence") == 2


async def test_suspension_times_out_after_thirty_days_from_row_clock() -> None:
    db = FakeDb(behaviors={"2": ["quota"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()  # no resume: 30 days pass
        assert result["status"] == "failed" and result["failure_reason"] == "quota_exhausted"


async def test_restart_continues_from_current_stage_and_skips_done_sections() -> None:
    db = FakeDb(status="generating", stage="stage2", outline_state="approved", outline_version=1)
    db.add_sections([("1", "intro", True), ("2", "scope", False), ("3", "price", False)])
    db.sections[0].update(state="complete", version=True)
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=True)
        assert (await handle.result())["status"] == "complete"
        assert s.db.count("save_section") == 2  # only the stage 2 sections
        assert s.db.count("save_outline") == 0 and s.db.count("call_model:facts") == 0


async def test_restart_of_gated_run_rearms_gate_from_row() -> None:
    db = FakeDb(
        status="awaiting_outline_approval",
        stage="outline",
        outline_state="ready",
        outline_version=1,
        gate_expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    db.add_sections([("1", "intro", True)])
    async with stack(db) as s:
        handle, _ = await s.start()
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "gate_expired"


async def test_restart_with_passed_gate_fails_at_once() -> None:
    db = FakeDb(
        status="awaiting_outline_approval",
        stage="outline",
        outline_state="ready",
        outline_version=1,
        gate_expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
    )
    db.add_sections([("1", "intro", True)])
    async with stack(db) as s:
        handle, _ = await s.start()
        assert (await handle.result())["failure_reason"] == "gate_expired"


async def test_restart_of_suspended_run_waits_and_resumes() -> None:
    db = FakeDb(
        status="suspended",
        stage="stage1",
        outline_state="approved",
        outline_version=1,
        failure_reason="quota_exhausted",
        suspended_at=datetime.now(UTC).isoformat(),
    )
    db.add_sections([("1", "intro", True), ("2", "scope", False)])
    async with stack(db) as s:
        handle, _ = await s.start()
        await asyncio.sleep(0.3)
        await s.resume(handle)
        assert (await handle.result())["status"] == "complete"


async def test_run_gone_ends_quietly() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        s.db.gone = True  # a purge erased the rows
        await handle.signal("approve_outline", {"outline_version": 1})
        result = await handle.result()
        assert result["status"] == "gone"
        assert s.db.count("fail_run") == 0


async def test_foundation_failure_fails_run_with_the_sections_own_reason() -> None:
    db = FakeDb(behaviors={"1": ["inference"] * 3})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "inference_unavailable"


async def test_stage2_failure_leaves_run_complete() -> None:
    db = FakeDb(behaviors={"3": ["inference"] * 3})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        assert (await handle.result())["status"] == "complete"
        assert [x["state"] for x in s.db.sections] == ["complete", "complete", "failed"]


async def test_every_section_failing_fails_run_internal() -> None:
    db = FakeDb(
        behaviors={"2": ["inference"] * 3, "3": ["inference"] * 3},
        skeleton=[("2", "scope", False), ("3", "price", False)],
    )
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert (
            result["status"] == "failed" and result["failure_reason"] == "internal"
        )  # NothingToMint


async def test_residency_refusal_is_terminal() -> None:
    db = FakeDb(behaviors={"2": ["residency"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["failure_reason"] == "residency_capacity"
        assert s.db.count("call_model:2") == 1  # never retried (WP-8)


async def test_scope_revoked_before_stage1_fails_the_run_with_no_section_written() -> None:
    db = FakeDb(
        behaviors={"scope": ["ok", "scope"]}
    )  # outline's re-resolve passes, check_scope is empty
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "scope_revoked"
        assert db.count("save_section") == 0 and db.count("fail_run") == 1


async def test_scope_revoked_in_a_section_ends_the_run_and_keeps_completed_sections() -> None:
    # re-resolves: outline, check_scope, foundation section, capture_facts, then a stage 2 section
    db = FakeDb(behaviors={"scope": ["ok", "ok", "ok", "ok", "scope"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await handle.result()
        assert result["status"] == "failed" and result["failure_reason"] == "scope_revoked"
        assert (
            db.sections[0]["state"] == "complete" and db.sections[0]["version"]
        )  # durable (ASYNC-15)
        assert any(x["state"] == "failed" for x in db.sections[1:])


async def test_poison_breaker_stops_the_run() -> None:
    """The breaker trips and the parent cancels the sections still retrying.

    Runs on the real clock (``local_stack``), not the time-skipping server: that server skips the
    retry backoff, so a cancelled section's last attempt fails at the same instant as the cancel
    and the server answers the cancel command with ``ACTIVITY_UNKNOWN``, leaving the child hung
    (see ``test_cancel_race.py``).
    """
    skeleton = [("0", "intro", True)] + [(str(i), "scope", False) for i in range(1, 9)]
    db = FakeDb(skeleton=skeleton, behaviors={str(i): ["inference"] * 3 for i in range(1, 9)})
    async with local_stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        result = await asyncio.wait_for(handle.result(), 90)
        assert result["status"] == "failed" and result["failure_reason"] == "inference_unavailable"


async def test_regeneration_skips_outline_and_gate() -> None:
    db = FakeDb(status="pending")
    db.add_sections([("1", "intro", True), ("2", "scope", False)])
    for sec in db.sections:
        sec.update(state="complete", version=True)
    async with stack(db) as s:
        handle, _ = await s.start(
            wf_id="regen:abc",
            origin="regeneration",
            outline_gate=False,
            base_version_id=str(__import__("uuid").uuid4()),
        )
        assert (await handle.result())["status"] == "complete"
        assert s.db.count("save_outline") == 0
        assert s.db.count("save_section") == 2


async def test_coherence_conflict_is_surfaced_not_retried() -> None:
    db = FakeDb(conflict_value="250")
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        assert (await handle.result())["status"] == "complete"
        assert s.db.coherence == "conflicted" and s.db.conflicts >= 1
        assert s.db.count("call_model:coherence") == 1


async def test_gap_section_does_not_fail_run() -> None:
    db = FakeDb(behaviors={"retrieve:price": ["insufficient", "insufficient"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        assert (await handle.result())["status"] == "complete"
        assert s.db.sections[2]["state"] == "gap" and s.db.count("retrieve") >= 4


async def test_weak_verification_retries_up_to_the_ceiling() -> None:
    db = FakeDb(behaviors={"verify:2": ["weak", "weak", "weak"]})
    async with stack(db) as s:
        handle, _ = await s.start(outline_gate=False)
        assert (await handle.result())["status"] == "complete"
        assert s.db.count("call_model:2") == 3  # one draft and two retries (ASV-2)
