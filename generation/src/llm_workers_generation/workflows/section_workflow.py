"""``SectionWorkflow`` (LLD §6.2): one section, its own history (WP-1).

Body: acquire the tenant's slot, run ``write_section`` and release the slot in a ``finally``.
Success returns the outcome the activity already wrote. A quota refusal and a terminal run come
back as results. A permanent failure runs ``fail_section`` and returns a failed outcome; the
parent applies LW-5.

Replay safety: child id, queue and policies are fixed by the parent (WW-5). Security: the section
id and enums only. Versioning: pinned (WV-6).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, VersioningBehavior
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from llm_models.workers import WORKFLOW_SECTION, SectionJob, SectionOutcome

    from llm_workers_generation import errors, tuning
    from llm_workers_generation.queues import control_queue
    from llm_workers_generation.types import FailSectionRequest, SlotAcquire, SlotLease, StepResult
    from llm_workers_generation.workflows._common import WORK, WRITE, error_type


@workflow.defn(name=WORKFLOW_SECTION, versioning_behavior=VersioningBehavior.PINNED)
class SectionWorkflow:
    """Acquire slot, write the section, release the slot."""

    @workflow.run
    async def run(self, job: SectionJob) -> SectionOutcome:
        """Return how the section ended. Never raises for an expected outcome."""
        info = workflow.info()
        queue, control = info.task_queue, control_queue(info.task_queue)
        slot = SlotAcquire(dict(job.run_input), queue, info.workflow_id)
        waited: float | None = None  # stays None if the slot is never granted
        try:
            try:
                waited = await self._acquire(slot, control)
            except ActivityError as exc:
                if is_cancelled_exception(exc):
                    raise
                outcome = await self._close_failed(job, queue, exc)  # no slot: close the section
            else:
                outcome = await self._write(job, queue)
        finally:  # every exit: success, failure, cancellation. Also withdraws a waiting request
            # the wait is reported by release_slot (an activity records it on fastapicommon)
            await self._release(replace(slot, waited_seconds=waited), control)
        return outcome

    async def _acquire(self, slot: SlotAcquire, control: str) -> float:
        """Ask until granted; returns the seconds waited. The wait is a workflow timer (LW-16)."""
        started, delay = workflow.time(), 1.0
        while True:
            lease = await workflow.execute_activity(
                "acquire_slot",
                slot,
                result_type=SlotLease,
                task_queue=control,
                start_to_close_timeout=timedelta(seconds=tuning.CONTROL_START_TO_CLOSE_SECONDS),
                retry_policy=RetryPolicy(maximum_attempts=tuning.CONTROL_READ_MAX_ATTEMPTS),
                cancellation_type=workflow.ActivityCancellationType.TRY_CANCEL,
            )
            if lease.granted:
                return workflow.time() - started
            await workflow.sleep(delay)  # the request keeps its place in the tenant's queue
            delay = min(delay * 2, float(tuning.ACQUIRE_BACKOFF_MAX_SECONDS))

    async def _write(self, job: SectionJob, queue: str) -> SectionOutcome:
        try:
            written: SectionOutcome = await workflow.execute_activity(
                "write_section",
                job,
                result_type=SectionOutcome,
                task_queue=queue,
                start_to_close_timeout=timedelta(seconds=WORK.start_to_close),
                heartbeat_timeout=timedelta(seconds=tuning.ACTIVITY_HEARTBEAT_SECONDS),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=WORK.initial),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=WORK.maximum),
                    maximum_attempts=WORK.attempts,
                    non_retryable_error_types=list(errors.NON_RETRYABLE),
                ),
                cancellation_type=workflow.ActivityCancellationType.TRY_CANCEL,
            )
            return written
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise  # the parent cancelled this section: not a failure, nothing to close (WB-6)
            return await self._close_failed(job, queue, exc)

    async def _close_failed(
        self, job: SectionJob, queue: str, exc: ActivityError
    ) -> SectionOutcome:
        """Close the section ``failed`` with the reason the activity error implies."""
        reason = errors.reason_for(error_type(exc))
        await workflow.execute_activity(
            "fail_section",
            FailSectionRequest(dict(job.run_input), job.section_id, reason, job.stage),
            result_type=StepResult,
            task_queue=control_queue(queue),
            start_to_close_timeout=timedelta(seconds=WRITE.start_to_close),
            retry_policy=RetryPolicy(maximum_attempts=WRITE.attempts),
        )
        return SectionOutcome(job.section_id, "failed", failure_reason=reason)

    async def _release(self, slot: SlotAcquire, control: str) -> None:
        """Release in a ``finally`` on every exit. An unreleased lease is reaped (§12.2)."""
        try:
            await workflow.execute_activity(
                "release_slot",
                slot,
                result_type=StepResult,
                task_queue=control,
                start_to_close_timeout=timedelta(seconds=WRITE.start_to_close),
                retry_policy=RetryPolicy(maximum_attempts=WRITE.attempts),
            )
        except ActivityError:
            pass  # the lease reaper frees it (TENANT_SLOT_LEASE_SECONDS)
