"""The one place an engine error becomes a Temporal result or failure (LLD §4.4, WA-6, WS-11).

``run_activity`` opens ``run_scope`` first (WA-1), runs the body, and maps what comes out:

* ``RunGone`` and ``RunTerminal`` are *results* (a flag), counted, never raised (P0-6, WB-6).
* ``QuotaRefused`` is a *result* where the activity can suspend (WA-17).
* everything else becomes ``ApplicationError(type=<code>, message=<fixed text>)`` with **no
  details and no chaining** (``from None``), so engine exception text never reaches history.

``asyncio.CancelledError`` is a ``BaseException`` and passes through untouched (WA-15).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from temporalio.exceptions import ApplicationError

from llm_workers_generation import errors
from llm_workers_generation.activities.engine_api import ERRORS, EngineApiGap, PackOutlineMissing
from llm_workers_generation.activities.heartbeat import heartbeating
from llm_workers_generation.activities.ports import Ctx, EngineApi
from llm_workers_generation.observability.app_metrics import AppMetrics, app_metrics

# An exception from one of these modules is a store failure (retryable). The same modules are
# blocked from import by ``tests/unit/test_architecture.py``, whose list must contain every entry.
_STORE_MODULES = (
    "asyncpg",
    "psycopg",
    "psycopg2",
    "psycopg_pool",
    "aiopg",
    "sqlalchemy",
    "databases",
    "pgcommon",
    "boto3",
    "botocore",
    "aioboto3",
    "aiobotocore",
    "eventcommon",
)


def code_for(api: EngineApi, exc: Exception) -> tuple[str, bool]:
    """``(error type, non_retryable)`` for an exception, per the §4.4 table."""
    mapped = api.translate(exc)
    if isinstance(mapped, ERRORS.ScopeRevoked):
        return errors.ERR_SCOPE_REVOKED, True
    if isinstance(mapped, ERRORS.ResidencyRefused):
        return errors.ERR_RESIDENCY, True
    if isinstance(mapped, ERRORS.SchemaViolation):
        return errors.ERR_SCHEMA, True
    if isinstance(mapped, ERRORS.NothingToMint):
        return errors.ERR_NOTHING_TO_MINT, True
    if isinstance(mapped, ERRORS.InferenceUnavailable):
        return errors.ERR_INFERENCE, False
    if isinstance(mapped, EngineApiGap | PackOutlineMissing):
        return errors.ERR_INTERNAL, True
    if isinstance(mapped, ERRORS.ContentUnavailable):
        # A failed read of a section's text (``load_section_text``). The engine turns every read
        # failure into this one error, a transient store or database blip included, so it is
        # retryable: the activity's bounded attempts (3, ``tuning.ACTIVITY_MAX_ATTEMPTS``) ride
        # out a blip, and a permanent fault (a digest mismatch) ends as a recorded ``internal``
        # failure (ADR-0003: the section fails and the run follows the usual rules). Not an
        # ``Internal`` fall-through by accident: this is a decision, pinned by a test.
        return errors.ERR_INTERNAL, False
    if type(mapped).__module__.split(".")[0] in _STORE_MODULES:
        return errors.ERR_STORE, False
    return errors.ERR_INTERNAL, False


def safe_error(code: str, *, non_retryable: bool) -> ApplicationError:
    """A content-free failure: fixed message, no details (WS-11)."""
    return ApplicationError(errors.FIXED_MESSAGES[code], type=code, non_retryable=non_retryable)


async def run_activity[R](
    api: EngineApi,
    metrics: AppMetrics,
    name: str,
    run_input: dict[str, Any],
    body: Callable[[Ctx], Awaitable[R]],
    *,
    gone: R,
    terminal: R,
    quota: R | None = None,
    beat: tuple[str | int, ...] | None = None,
) -> R:
    """Run ``body`` in the run's scope with heartbeats (if ``beat``) and the §4.4 mapping."""
    started = time.monotonic()
    try:
        async with api.scope(run_input) as ctx:
            if beat is None:
                return await body(ctx)
            async with heartbeating(*beat):
                return await body(ctx)
    except Exception as exc:
        mapped = api.translate(exc)
        if isinstance(mapped, ERRORS.RunGone):
            metrics.gone(name)
            return gone
        if isinstance(mapped, ERRORS.RunTerminal):
            return terminal
        if isinstance(mapped, ERRORS.QuotaRefused) and quota is not None:
            return quota
        code, non_retryable = code_for(api, exc)
        metrics.activity_failed(name, code)
        raise safe_error(code, non_retryable=non_retryable) from None
    finally:
        metrics.activity(name, time.monotonic() - started)


def activity_metrics() -> AppMetrics:
    """The process's activity-side instruments, on fastapicommon (labels are bounded, WS-7)."""
    return app_metrics()
