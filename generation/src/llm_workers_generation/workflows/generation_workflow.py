"""``GenerationWorkflow`` (LLD §6.1, §6.4, §8, §9.1): a long-form document run.

Phases, in order: outline, gate, stage 1 (foundation sections), facts, stage 2, coherence, finish.
A regeneration (A-5) skips the outline and the gate. A restart (W-8, orphan restart) reads the
row first and continues from ``current_stage`` (RUN-8).

* **A signal is a hint, the row is the truth.** Every wait wakes on a signal or every
  ``WAIT_RECONCILE_SECONDS`` and reads ``run_state`` (WW-1, WG-7). A lost approve costs at most
  one interval.
* **Quota is a phase result.** A refused step suspends the run and repeats that phase on resume.
* **Cancel ends the run without writing it** (WG-4): the API service wrote the edges (W-5).
* **LW-5:** a foundation failure fails the run with the sections' common reason; a stage 2 failure
  leaves the section ``failed`` and the run ``complete``. WW-3 (deadline) and WW-4 (poison breaker)
  bound a run that cannot finish.

Replay safety: all time is workflow time; every loop waits on a condition, a timer or an
activity (WDT-5); fan-out uses an ``asyncio.Semaphore`` (deterministic). The signal handlers only
record. Versioning: auto-upgrade (LW-12): a change to the order or number of commands needs a
``workflow.patched`` marker (``versioning/patches.py``, WV-1). Security: ids and enums only; the
reviewer's words never enter (WS-10).
"""

from __future__ import annotations

import asyncio
from typing import Any

from temporalio import workflow
from temporalio.common import VersioningBehavior, WorkflowIDReusePolicy
from temporalio.exceptions import ChildWorkflowError
from temporalio.workflow import ChildWorkflowCancellationType, ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from llm_models.workers import (
        WORKFLOW_GENERATION,
        WORKFLOW_SECTION,
        GenerationWorkflowInput,
        OutlineSaved,
        RunResult,
        RunState,
        SectionJob,
        SectionOutcome,
        SectionRef,
    )

    from llm_workers_generation import tuning
    from llm_workers_generation.types import (
        CoherenceStep,
        CompleteRequest,
        FactsRequest,
        MintStep,
        OutlineRequest,
        OutlineStep,
        RunRequest,
        ScopeCheck,
        StageRequest,
        StepResult,
    )
    from llm_workers_generation.workflows._common import (
        PHASE,
        READ,
        WRITE,
        End,
        Fail,
        Quota,
        RunWorkflowBase,
        parse_time,
    )

OUTLINE, GATE, STAGE1, FACTS, STAGE2, COHERENCE, FINISH, DONE = range(8)
_PHASE_FOR_STAGE = {"outline": STAGE1, "stage1": STAGE1, "stage2": STAGE2, "coherence": COHERENCE}
_SETTLED = frozenset({"complete", "failed", "gap"})
# a section failing for one of these ends the whole run, at any stage (§4.4)
_RUN_ENDING = frozenset({"scope_revoked", "residency_capacity", "schema_violation"})


@workflow.defn(name=WORKFLOW_GENERATION, versioning_behavior=VersioningBehavior.AUTO_UPGRADE)
class GenerationWorkflow(RunWorkflowBase):
    """The document run."""

    def __init__(self) -> None:
        super().__init__()
        self._approved = -1
        self._revise_to = -1
        self._revision: tuple[tuple[str, str], ...] | None = None
        self._outline_version = 0
        self._saved: OutlineSaved | None = None
        self._done: set[str] = set()
        self._foundation_failures: dict[str, str] = {}
        self._stage2_ended = 0
        self._stage2_reasons: dict[str, int] = {}
        self._halt: str | None = None
        self._halt_reason = "internal"
        self._quota = False

    # -- signals (§8.1): record only; the loops decide ---------------------------------------
    @workflow.signal(name="approve_outline")
    def approve_outline(self, payload: dict[str, Any]) -> None:
        """A duplicate or a stale version is a no-op: the loop compares against the current one."""
        self._approved = max(self._approved, _version(payload))

    @workflow.signal(name="revise_outline")
    def revise_outline(self, payload: dict[str, Any]) -> None:
        """A revision at or below the version already acted on is stale (WG-2)."""
        version = _version(payload)
        if version <= self._outline_version or version <= self._revise_to:
            return
        self._revise_to = version
        self._revision = tuple((str(s["ref"]), str(s["type"])) for s in payload.get("sections", ()))

    # -- run ----------------------------------------------------------------------------------
    @workflow.run
    async def run(self, raw: dict[str, Any]) -> RunResult:
        """Run the document. The result is small; callers read the tables (ASYNC-5)."""
        inp = GenerationWorkflowInput.from_dict(raw)
        self._begin(raw, "document")
        try:
            result = await self._execute(inp)
        except End as end:
            result = end.result
        except Fail as fail:
            result = await self._write_failure(fail)
        return self._finish(result)

    async def _execute(self, inp: GenerationWorkflowInput) -> RunResult:
        phase = await self._initial_phase(inp)
        while phase < DONE:
            self._check_cancel()
            self._check_deadline()
            try:
                phase = await self._run_phase(phase, inp)
            except Quota as quota:
                await self._suspend(quota.phase)  # then the same phase again, from the row
        return RunResult("complete")

    async def _initial_phase(self, inp: GenerationWorkflowInput) -> int:
        """Where a first start or a restart continues (§6.1 "restart check")."""
        state = await self._state()
        if state.status == "suspended":
            self._stage = state.stage
            await self._wait_suspended()
            state = await self._state()
        if state.status == "awaiting_outline_approval":
            self._saved = OutlineSaved(
                tuple(s.section_id for s in state.section_refs),
                state.outline_version,
                state.gate_expires_at,
            )
            self._outline_version = state.outline_version or 0
            return GATE
        if state.status == "outline_building":
            return OUTLINE
        if state.status == "pending":
            return STAGE1 if inp.is_regeneration else OUTLINE
        return _PHASE_FOR_STAGE.get(state.stage or "", STAGE1)

    async def _run_phase(self, phase: int, inp: GenerationWorkflowInput) -> int:
        if phase == OUTLINE:
            return await self._outline(inp)
        if phase == GATE:
            return await self._gate()
        if phase == STAGE1:
            return await self._stage1()
        if phase == FACTS:
            return await self._facts()
        if phase == STAGE2:
            return await self._stage2()
        if phase == COHERENCE:
            return await self._coherence()
        return await self._finish_run()

    # -- phases -------------------------------------------------------------------------------
    async def _begin_stage(self, stage: str) -> None:
        self._stage = stage
        res = await self._call("begin_stage", StageRequest(self._raw, stage), StepResult, WRITE)
        await self._require(res.gone, res.skipped_terminal or not res.ok)

    async def _require(self, gone: bool, terminal: bool) -> None:
        """A gone run ends quietly; a terminal one ends unwritten (§9.5, WB-6)."""
        if gone:
            raise End(RunResult("gone"))
        if terminal:
            await self._state()  # raises End with the row's final status
            raise Fail("internal", self._stage)  # the row is not terminal: record the failure

    async def _outline(self, inp: GenerationWorkflowInput) -> int:
        self._stage = "outline"
        gated = inp.outline_gate and not inp.is_regeneration
        if gated:
            await self._begin_stage("outline")
        revised, self._revision = self._revision, None
        step = await self._call("outline", OutlineRequest(self._raw, revised), OutlineStep, PHASE)
        await self._require(step.gone, step.skipped_terminal or step.saved is None)
        assert step.saved is not None  # noqa: S101 - _require ended the run otherwise
        self._saved = step.saved
        self._outline_version = step.saved.outline_version or 0
        return GATE if gated else STAGE1

    async def _gate(self) -> int:
        """Wait for the reviewer: signal, timer, or the row (§8.2, WG-7, WG-8)."""
        self._stage = "outline"
        assert self._saved is not None  # noqa: S101
        acted = self._outline_version
        deadline = parse_time(self._saved.gate_expires_at)
        while True:
            interval = float(tuning.WAIT_RECONCILE_SECONDS)
            left = None if deadline is None else (deadline - workflow.now()).total_seconds()
            if left is not None:
                interval = min(interval, max(left, 0.0))
            signalled = False
            if left is None or left > 0:
                signalled = await self._sleep_until(
                    lambda: self._cancelled or self._approved >= acted or self._revise_to > acted,
                    interval,
                )
            state = await self._state()  # cancel, terminal and gone end here
            deadline = parse_time(state.gate_expires_at) or deadline
            if state.status == "generating":  # the approval won, also over the timer
                if not signalled:
                    self.metrics.recovered("gate")
                return STAGE1
            if state.status == "outline_building" and (
                (state.outline_version or 0) > acted or self._revise_to > acted
            ):
                if self._revise_to <= acted:
                    self.metrics.recovered("gate")
                return OUTLINE
            if state.status == "awaiting_outline_approval":
                if self._approved >= acted:
                    return STAGE1
                if deadline is not None and workflow.now() >= deadline:
                    raise Fail("gate_expired", "outline")
            if deadline is not None and workflow.now() >= deadline:
                # past the deadline the loop above does not sleep: do not re-read the row at once
                await workflow.sleep(tuning.GATE_RECHECK_SECONDS)

    async def _stage1(self) -> int:
        self._stage = "stage1"
        await self._check_scope()  # the gate may have run for weeks (SCOPE-3)
        await self._begin_stage("stage1")
        state = await self._state()
        await self._fan_out(
            [s for s in state.section_refs if s.is_foundation],
            "stage1",
            foundation=True,
            regen=self._is_regen(state),
        )
        return FACTS

    async def _facts(self) -> int:
        self._stage = "stage1"
        state = await self._state()
        ids = tuple(
            s.section_id for s in state.section_refs if s.is_foundation and s.state == "complete"
        )
        res = await self._call("capture_facts", FactsRequest(self._raw, ids), StepResult, PHASE)
        await self._require(res.gone, res.skipped_terminal)
        if res.quota_suspended:
            raise Quota("facts")
        return STAGE2

    async def _stage2(self) -> int:
        self._stage = "stage2"
        await self._begin_stage("stage2")
        state = await self._state()
        await self._fan_out(
            [s for s in state.section_refs if not s.is_foundation],
            "stage2",
            foundation=False,
            regen=self._is_regen(state),
        )
        return COHERENCE

    async def _coherence(self) -> int:
        await self._begin_stage("coherence")
        step = await self._call("coherence_pass", RunRequest(self._raw), CoherenceStep, PHASE)
        await self._require(step.gone, step.skipped_terminal)
        if step.quota_suspended:
            raise Quota("coherence")
        return FINISH

    async def _finish_run(self) -> int:
        self._stage = "coherence"
        mint = await self._call("mint_document_version", RunRequest(self._raw), MintStep, WRITE)
        await self._require(mint.gone, mint.skipped_terminal)
        if mint.minted is None:
            raise Fail("internal", self._stage)
        done = await self._terminal_write("complete_run", CompleteRequest(self._raw, mint.minted))
        await self._require(done.gone, not done.ok)
        return DONE

    async def _check_scope(self) -> None:
        res = await self._call("check_scope", RunRequest(self._raw), ScopeCheck, READ)
        await self._require(res.gone, res.skipped_terminal)

    def _is_regen(self, _state: RunState) -> bool:
        regenerating: bool = GenerationWorkflowInput.from_dict(self._raw).is_regeneration
        return regenerating

    # -- fan-out (§12.2: per document here, per tenant in TenantSlots) --------------------------
    def _needs_work(self, ref: SectionRef, regen: bool, foundation: bool) -> bool:
        if ref.section_id in self._done:
            return False
        if not regen and ref.state in _SETTLED:  # an original run wrote it already (ASYNC-14)
            if ref.state == "failed" and foundation:
                self._foundation_failures.setdefault(ref.section_id, "internal")
            return False
        return True

    async def _fan_out(
        self, refs: list[SectionRef], stage: str, *, foundation: bool, regen: bool
    ) -> None:
        todo = [r for r in refs if self._needs_work(r, regen, foundation)]
        if todo:
            sem = asyncio.Semaphore(tuning.FANOUT_MAX_PER_DOCUMENT)
            running = 0

            async def one(ref: SectionRef) -> None:
                nonlocal running
                async with sem:
                    if self._cancelled or self._halt or self._quota:
                        return  # stop starting new sections
                    running += 1
                    self.metrics.fanout("document", running)
                    try:
                        outcome = await workflow.execute_child_workflow(
                            WORKFLOW_SECTION,
                            SectionJob(ref.section_id, stage, dict(self._raw)),
                            id=f"{workflow.info().workflow_id}:s:{ref.section_id}:{stage}",
                            task_queue=self._queue,
                            result_type=SectionOutcome,
                            parent_close_policy=ParentClosePolicy.REQUEST_CANCEL,
                            cancellation_type=ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                        )
                    except ChildWorkflowError:
                        outcome = SectionOutcome(
                            ref.section_id, "failed", failure_reason="internal"
                        )
                    finally:
                        running -= 1
                        self.metrics.fanout("document", running)
                    self._record(outcome, foundation)

            tasks = [asyncio.create_task(one(r)) for r in todo]
            await workflow.wait_condition(
                lambda: all(t.done() for t in tasks) or self._cancelled or self._halt is not None
            )
            if not all(t.done() for t in tasks):  # cancel, halt: take the in-flight children down
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        await self._settle(stage, foundation)

    def _record(self, outcome: SectionOutcome, foundation: bool) -> None:
        if outcome.quota_suspended:
            self._quota = True
            return
        if outcome.skipped_terminal:
            self._halt = self._halt or "terminal"
            return
        self._done.add(outcome.section_id)
        if outcome.state == "failed":
            reason = outcome.failure_reason or "internal"
            reason = reason if reason in tuning.FAILURE_REASONS else "internal"
            if reason in _RUN_ENDING:
                self._halt, self._halt_reason = self._halt or "ending", reason
            elif foundation:
                self._foundation_failures[outcome.section_id] = reason
            else:
                self._stage2_ended += 1
                self._stage2_reasons[reason] = self._stage2_reasons.get(reason, 0) + 1
                self._check_poison()
        elif not foundation:
            self._stage2_ended += 1
        if self._halt is None and self._active_seconds() > tuning.RUN_MAX_SECONDS:
            self.metrics.run_deadline.add(1)
            self._halt, self._halt_reason = "deadline", "internal"

    def _check_poison(self) -> None:
        """WW-4: most ended stage 2 sections failed with one error type: stop spending tokens."""
        if self._stage2_ended < tuning.POISON_MIN_SECTIONS or self._halt:
            return
        reason, count = max(self._stage2_reasons.items(), key=lambda kv: kv[1])
        if count * 100 > tuning.POISON_THRESHOLD_PERCENT * self._stage2_ended:
            self.metrics.breaker(reason)
            self._halt, self._halt_reason = "poison", reason

    async def _settle(self, stage: str, foundation: bool) -> None:
        """Turn what the fan-out saw into the run's next step, strongest cause first."""
        self._check_cancel()
        if self._halt == "terminal":
            await self._state()
            raise End(RunResult("failed", "internal"))
        if self._halt in ("ending", "poison", "deadline"):
            raise Fail(self._halt_reason, stage)
        if foundation and self._foundation_failures:
            reasons = set(self._foundation_failures.values())
            raise Fail(reasons.pop() if len(reasons) == 1 else "internal", stage)
        if self._quota:
            self._quota = False
            raise Quota(stage)


def _version(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("outline_version", -1))
    except TypeError, ValueError:
        return -1
