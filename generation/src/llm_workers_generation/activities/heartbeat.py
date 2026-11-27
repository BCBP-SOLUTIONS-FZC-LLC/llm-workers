"""Background heartbeats (LLD WA-14, WA-16): the heartbeat never depends on the call.

A non-streaming Gateway call or a verification pass can run longer than the 30-second heartbeat
timeout with no point at which the code could heartbeat. This context manager beats every
``ACTIVITY_HEARTBEAT_SECONDS / 3`` from a background task for the life of the activity. A
cancellation reaches the activity through the SDK at the next beat (TRY_CANCEL, WW-5).

Heartbeat details are identifiers and counts only, never text (WS-11).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from temporalio import activity

from llm_workers_generation import tuning

INTERVAL_SECONDS = tuning.ACTIVITY_HEARTBEAT_SECONDS / 3


@contextlib.asynccontextmanager
async def heartbeating(
    *details: str | int, interval: float = INTERVAL_SECONDS
) -> AsyncIterator[None]:
    """Beat in the background while the block runs."""

    async def beat() -> None:
        while True:
            await asyncio.sleep(interval)
            activity.heartbeat(*details)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
