"""``acquire_slot`` and ``release_slot``: update-with-start on ``TenantSlots`` (LLD §12.2).

A workflow cannot start or signal another workflow, so the section child calls these activities.
``acquire_slot`` starts the entity if it is not running and queues the request first in, first
out. It waits at most ``ACQUIRE_POLL_SECONDS`` (2 s) and answers ``granted=False`` if no slot
freed. The section workflow then sleeps (a workflow timer, backing off to 15 s) and asks again,
keeping its place (the request id is stable). Waiting never holds a slot of the 16-slot control
worker, which must stay free for approvals and terminal writes (LW-16, G-6).
``release_slot`` frees a lease or withdraws a waiting
request and is called in a ``finally`` (WW-5).

Assumption (ADR-0005): the per-tenant bound is per queue, not across queues (WOQ-10).
"""

from __future__ import annotations

import asyncio
import re

from temporalio import activity
from temporalio.client import Client, WithStartWorkflowOperation, WorkflowUpdateStage
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import TemporalError
from temporalio.service import RPCError

from llm_workers_generation import errors, tuning
from llm_workers_generation.activities._guard import safe_error
from llm_workers_generation.observability.app_metrics import app_metrics
from llm_workers_generation.queues import tier_of_queue
from llm_workers_generation.types import (
    SlotAcquire,
    SlotLease,
    SlotRequest,
    StepResult,
    TenantSlotsInput,
)
from llm_workers_generation.workflows.tenant_slots_workflow import TenantSlots

_TENANT = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _tenant(run_input: dict[str, object]) -> str:
    """The run's tenant id, or a fixed non-retryable error (an odd id would share one bucket)."""
    tenant = run_input.get("tenant_id")
    if not isinstance(tenant, str) or not _TENANT.fullmatch(tenant):
        raise safe_error(errors.ERR_INTERNAL, non_retryable=True)
    return tenant


def slots_workflow_id(queue: str, tenant_id: str) -> str:
    """``slots:{queue}:{tenant_id}`` (§5.2)."""
    return f"slots:{queue}:{tenant_id}"


class SlotActivities:
    """The two slot activities, over a Temporal client."""

    def __init__(self, client: Client) -> None:
        self._client = client

    @activity.defn(name="acquire_slot")
    async def acquire_slot(self, req: SlotAcquire) -> SlotLease:
        """Queue for a slot; the lease comes back when one is free or after the poll window."""
        tenant = _tenant(req.run_input)
        start = WithStartWorkflowOperation(
            TenantSlots.run,
            TenantSlotsInput(
                req.queue, tenant, tuning.FANOUT_MAX_PER_TENANT, tuning.TENANT_SLOT_LEASE_SECONDS
            ),
            id=slots_workflow_id(req.queue, tenant),
            task_queue=req.queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        try:
            handle = await self._client.start_update_with_start_workflow(
                TenantSlots.acquire,
                SlotRequest(req.request_id),
                start_workflow_operation=start,
                wait_for_stage=WorkflowUpdateStage.ACCEPTED,
            )
            return await asyncio.wait_for(
                handle.result(), tuning.ACQUIRE_POLL_SECONDS + tuning.ACQUIRE_CLIENT_MARGIN_SECONDS
            )
        except TimeoutError:
            return SlotLease(req.request_id, False)
        except asyncio.CancelledError:
            raise
        except Exception:  # a client error names the workflow id (a tenant id): never let it out
            raise safe_error(errors.ERR_INTERNAL, non_retryable=False) from None

    @activity.defn(name="release_slot")
    async def release_slot(self, req: SlotAcquire) -> StepResult:
        """Free the lease or withdraw the wait. A missing entity means nothing is held."""
        tenant = _tenant(req.run_input)
        handle = self._client.get_workflow_handle(slots_workflow_id(req.queue, tenant))
        if req.waited_seconds is not None:  # the slot was granted: report the wait (fastapicommon)
            app_metrics().slot_waited(tier_of_queue(req.queue).value, req.waited_seconds)
        try:
            found = await handle.execute_update(TenantSlots.release, SlotRequest(req.request_id))
        except RPCError, TemporalError:
            return StepResult(ok=False)
        return StepResult(ok=bool(found))
