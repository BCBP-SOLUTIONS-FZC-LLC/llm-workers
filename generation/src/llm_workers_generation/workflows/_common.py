"""What every run workflow shares: activity policies, the wait loop, failure and end handling.

LLD mapping: §5.6 (timeouts, retries, TRY_CANCEL), §8.5 and WW-1/WW-2 (one reconcile loop for
every wait), §9.1 (suspension), §13.2 (a terminal write that cannot be recorded), §4.4.

Assumptions: the run's row is the truth and a signal only wakes a wait (principle 8). A wait
reads ``run_state`` on every wake.

Replay safety: every decision uses ``workflow.now()``/``workflow.time()``; no I/O; the
constants come from ``tuning`` (ADR-0004). Security: only identifiers, enums and counts are
passed to activities; failure text is never read (only the ``ApplicationError`` type is).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from llm_models.workers import RunResult, RunState

    from llm_workers_generation import errors, tuning
    from llm_workers_generation.observability.metrics import Metrics
    from llm_workers_generation.queues import control_queue
    from llm_workers_generation.types import (
        FailRequest,
        RunRequest,
        RunStateResult,
        StepResult,
    )

R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class Policy:
    """One row of the §5.6 table."""

    start_to_close: int
    attempts: int
    initial: int
    maximum: int
    heartbeat: int | None
    control: bool
    schedule_to_close: int | None = None
    """Total time across attempts; with ``attempts=0`` it is what ends an unbounded retry."""


WORK = Policy(
    tuning.ACTIVITY_START_TO_CLOSE_SECONDS,
    tuning.ACTIVITY_MAX_ATTEMPTS,
    2,
    30,
    tuning.ACTIVITY_HEARTBEAT_SECONDS,
    False,
)
PHASE = Policy(
    tuning.OUTLINE_START_TO_CLOSE_SECONDS,
    tuning.ACTIVITY_MAX_ATTEMPTS,
    2,
    30,
    tuning.ACTIVITY_HEARTBEAT_SECONDS,
    False,
)
WRITE = Policy(
    tuning.CONTROL_START_TO_CLOSE_SECONDS, tuning.CONTROL_WRITE_MAX_ATTEMPTS, 1, 10, None, True
)
# The control read is the heartbeat of every wait, including a 30-day suspension: a store or pool
# outage must not fail the run, so it retries (every 10 s at most) for a window, not three times.
# Errors that cannot succeed (``errors.NON_RETRYABLE``) still end it at once.
READ = Policy(
    tuning.CONTROL_START_TO_CLOSE_SECONDS,
    0,
    1,
    10,
    None,
    True,
    tuning.READ_RETRY_WINDOW_SECONDS,
)


class End(Exception):  # noqa: N818
    """Unwinds to the top of ``run`` where the workflow ends. Not an error."""

    def __init__(self, result: RunResult) -> None:
        super().__init__("end")
        self.result = result


class Fail(Exception):  # noqa: N818
    """Unwinds to the top of ``run`` where ``fail_run`` is written, once."""

    def __init__(self, reason: str, stage: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage


class Quota(Exception):  # noqa: N818
    """A model-calling step was refused for quota: suspend, wait, repeat the phase (§9.1)."""

    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


def error_type(exc: BaseException) -> str:
    """The ``ApplicationError`` type at the bottom of a failure chain. Never the message."""
    cause: BaseException | None = exc
    for _ in range(8):
        if isinstance(cause, ApplicationError):
            return cause.type or errors.ERR_INTERNAL
        cause = cause.__cause__ if cause is not None else None
    return errors.ERR_INTERNAL


def parse_time(value: str | None) -> datetime | None:
    """An ISO timestamp from the row, as an aware datetime."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class RunWorkflowBase:
    """Signals ``cancel`` and ``resume``, the activity helpers and the wait loop."""

    def __init__(self) -> None:
        self._cancelled = False
        self._resume = False
        self._stage: str | None = None
        self._kind = "document"
        self._raw: dict[str, Any] = {}
        self._queue = ""
        self._started_at = 0.0
        self._waited = 0.0
        self._metrics: Metrics | None = None

    # -- signals: a hint, never the truth (§8.1) ---------------------------------------------
    @workflow.signal(name="cancel")
    def cancel(self, payload: dict[str, Any] | None = None) -> None:
        """Duplicate cancels are no-ops (WG-3). The reason is never stored."""
        self._cancelled = True

    @workflow.signal(name="resume")
    def resume(self) -> None:
        """Wakes the suspended wait. A resume for a run that is not suspended is ignored (WG-6)."""
        self._resume = True

    # -- setup ---------------------------------------------------------------------------------
    def _begin(self, raw: dict[str, Any], kind: str) -> None:
        # the reviewer's words never travel on: only the reference does (WS-10, ASYNC-12)
        self._raw, self._kind = {k: v for k, v in raw.items() if k != "instruction"}, kind
        self._queue = workflow.info().task_queue
        self._started_at = workflow.time()
        self._metrics = Metrics(workflow.metric_meter())
        self._metrics.started(kind)

    @property
    def metrics(self) -> Metrics:
        """The workflow's instruments."""
        assert self._metrics is not None  # noqa: S101 - set by _begin
        return self._metrics

    # -- activities ---------------------------------------------------------------------------
    async def _act(self, name: str, arg: Any, result_type: type[R], policy: Policy) -> R:
        """Schedule one activity, with TRY_CANCEL (WW-5) and the §5.6 policy."""
        result: R = await workflow.execute_activity(
            name,
            arg,
            result_type=result_type,
            task_queue=control_queue(self._queue) if policy.control else self._queue,
            start_to_close_timeout=timedelta(seconds=policy.start_to_close),
            schedule_to_close_timeout=None
            if policy.schedule_to_close is None
            else timedelta(seconds=policy.schedule_to_close),
            heartbeat_timeout=None
            if policy.heartbeat is None
            else timedelta(seconds=policy.heartbeat),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=policy.initial),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=policy.maximum),
                maximum_attempts=policy.attempts,
                non_retryable_error_types=list(errors.NON_RETRYABLE),
            ),
            cancellation_type=workflow.ActivityCancellationType.TRY_CANCEL,
        )
        return result

    async def _call(self, name: str, arg: Any, result_type: type[R], policy: Policy) -> R:
        """``_act`` that turns a failed activity into the run failure it implies (§4.4)."""
        try:
            return await self._act(name, arg, result_type, policy)
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise  # a cancellation is never a failure of the run
            raise Fail(errors.reason_for(error_type(exc)), self._stage) from None

    async def _state(self) -> RunState:
        """Read the row. A gone run ends quietly (§9.5); a terminal one ends unwritten (WB-6)."""
        self._check_cancel()
        res = await self._call("run_state", RunRequest(self._raw), RunStateResult, READ)
        if res.gone or res.state is None:
            raise End(RunResult("gone"))
        if res.state.status in tuning.TERMINAL_STATUSES:
            raise End(RunResult(res.state.status, res.state.failure_reason))
        return res.state

    def _check_cancel(self) -> None:
        """``cancel`` ends the workflow without writing the run (§8.4, WG-4)."""
        if self._cancelled:
            raise End(RunResult("cancelled"))

    # -- time ---------------------------------------------------------------------------------
    def _active_seconds(self) -> float:
        """Workflow time spent working, outside the gate and the suspension waits (WW-3)."""
        return workflow.time() - self._started_at - self._waited

    def _check_deadline(self) -> None:
        if self._active_seconds() > tuning.RUN_MAX_SECONDS:
            self.metrics.run_deadline.add(1)
            raise Fail("internal", self._stage)

    async def _sleep_until(self, condition: Callable[[], bool], seconds: float) -> bool:
        """Wait for ``condition`` or ``seconds``. Returns whether the condition held."""
        before = workflow.time()
        try:
            await workflow.wait_condition(condition, timeout=timedelta(seconds=max(seconds, 0.001)))
            return True
        except TimeoutError:
            return False
        finally:
            self._waited += workflow.time() - before

    # -- suspension (§9.1, §8.5) ---------------------------------------------------------------
    async def _suspend(self, phase: str) -> None:
        """Suspend the run for quota, then wait until the row says it is running again."""
        res = await self._call("suspend_for_quota", RunRequest(self._raw), StepResult, WRITE)
        if res.gone:
            raise End(RunResult("gone"))
        self._resume = False
        await self._wait_suspended()

    async def _wait_suspended(self) -> None:
        """One reconcile loop: wake on a signal or every interval, read the row, act (WW-1)."""
        while True:
            state = await self._state()
            if state.status == "generating":
                if not self._resume:
                    self.metrics.recovered("suspended")  # the signal was lost, the row was not
                self._resume = False
                return
            remaining: float = tuning.WAIT_RECONCILE_SECONDS
            if state.status == "suspended":
                since = parse_time(state.suspended_at)
                if since is not None:
                    left = (
                        since + timedelta(days=tuning.SUSPEND_MAX_DAYS) - workflow.now()
                    ).total_seconds()
                    if left <= 0:
                        raise Fail("quota_exhausted", self._stage)  # the clock is the row's (G-3)
                    remaining = min(remaining, left)
            await self._sleep_until(lambda: self._cancelled or self._resume, remaining)
            self._resume = False if state.status == "suspended" else self._resume

    # -- failure (§13.2) ----------------------------------------------------------------------
    async def _write_failure(self, fail: Fail) -> RunResult:
        """``fail_run`` once, retried for ``TERMINAL_WRITE_RETRY_MINUTES`` before giving up."""
        await self._terminal_write("fail_run", FailRequest(self._raw, fail.reason, fail.stage))
        return RunResult("failed", fail.reason)

    async def _terminal_write(self, name: str, arg: Any) -> StepResult:
        try:
            written: StepResult = await workflow.execute_activity(
                name,
                arg,
                result_type=StepResult,
                task_queue=control_queue(self._queue),
                start_to_close_timeout=timedelta(seconds=tuning.CONTROL_START_TO_CLOSE_SECONDS),
                schedule_to_close_timeout=timedelta(minutes=tuning.TERMINAL_WRITE_RETRY_MINUTES),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(minutes=5),
                ),
            )
            return written
        except ActivityError:
            self.metrics.unrecorded_failures.add(1)
            raise ApplicationError(
                "terminal write not recorded", type="UnrecordedFailure", non_retryable=True
            ) from None

    def _finish(self, result: RunResult) -> RunResult:
        """Metrics at the end of a run. ``gone`` is not a failure and pages nobody (§9.5)."""
        self.metrics.completed(
            self._kind,
            result.status,
            workflow.time() - self._started_at,
            workflow.info().get_current_history_length(),
        )
        return result
