"""``TenantSlots`` (LLD §12.2, LW-8): the per-tenant fan-out bound as an entity workflow.

One long-lived workflow per tenant per queue, ``slots:{queue}:{tenant_id}``, started by
update-with-start. ``acquire`` is an update with a validator and a response: it queues the request
first in, first out and returns when a slot is free. ``release`` frees a lease or withdraws a
waiting request. A reaper frees an expired lease so a crashed holder cannot block the tenant.
Continue-as-new only where Temporal suggests it, after the handlers have finished, carrying the
live leases and the queue in order (WW-2, LW-13).

Replay safety: time is ``workflow.time()``; configuration arrives in the input (WDT-3).
Security: request ids and a tenant id only. Versioning: auto-upgrade (WV-6, LW-12).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from llm_workers_generation import tuning
    from llm_workers_generation.types import SlotLease, SlotRequest, TenantSlotsInput

WORKFLOW_TENANT_SLOTS = "TenantSlots"
MAX_REQUEST_ID = 256


@workflow.defn(name=WORKFLOW_TENANT_SLOTS, versioning_behavior=VersioningBehavior.AUTO_UPGRADE)
class TenantSlots:
    """FIFO slots for one tenant on one queue."""

    @workflow.init
    def __init__(self, inp: TenantSlotsInput) -> None:
        # state is built here, not in ``run``: with update-with-start the first ``acquire``
        # handler can run before ``run`` has started, and must find the queue it appends to
        self._limit, self._lease_seconds = inp.limit, inp.lease_seconds
        self._leases: dict[str, float] = dict(inp.leases)
        self._waiting: list[str] = list(inp.waiting)
        self._dirty = False
        self._draining = False

    @workflow.run
    async def run(self, inp: TenantSlotsInput) -> None:
        """Grant, reap and (when suggested) continue as new. Never returns otherwise."""
        while True:
            self._reap()
            self._grant()
            timeout = self._next_expiry()
            try:
                await workflow.wait_condition(lambda: self._dirty, timeout=timeout)
            except TimeoutError:
                pass
            self._dirty = False
            if workflow.info().is_continue_as_new_suggested():
                self._draining = True  # waiting acquires answer "not granted" and ask again
                await workflow.wait_condition(workflow.all_handlers_finished)
                workflow.continue_as_new(
                    TenantSlotsInput(
                        inp.queue,
                        inp.tenant_id,
                        self._limit,
                        self._lease_seconds,
                        tuple(self._leases.items()),
                        tuple(self._waiting),
                    )
                )

    def _grant(self) -> None:
        while self._waiting and len(self._leases) < self._limit:
            self._leases[self._waiting.pop(0)] = workflow.time() + self._lease_seconds

    def _reap(self) -> None:
        now = workflow.time()
        for rid in [r for r, expiry in self._leases.items() if expiry <= now]:
            del self._leases[rid]

    def _next_expiry(self) -> timedelta | None:
        if not self._leases:
            return None
        return timedelta(seconds=max(min(self._leases.values()) - workflow.time(), 0.001))

    @workflow.update
    async def acquire(self, req: SlotRequest) -> SlotLease:
        """Queue the request (idempotent by id); answer when it holds a slot or after the poll time.

        The wait is bounded here, in the workflow, so a request that keeps asking leaves no handler
        behind: each ask ends with the answer ``granted=False`` and the request keeps its place.
        """
        rid = req.request_id
        if rid not in self._leases and rid not in self._waiting:
            self._waiting.append(rid)
        self._dirty = True
        try:
            await workflow.wait_condition(
                lambda: rid in self._leases or rid not in self._waiting or self._draining,
                timeout=timedelta(seconds=tuning.ACQUIRE_POLL_SECONDS),
            )
        except TimeoutError:
            return SlotLease(rid, False)
        if rid in self._leases:
            return SlotLease(rid, True, self._leases[rid])
        return SlotLease(rid, False)

    @acquire.validator
    def _validate_acquire(self, req: SlotRequest) -> None:
        if not req.request_id or len(req.request_id) > MAX_REQUEST_ID:
            raise ValueError("invalid request id")  # rejected before it reaches history

    @workflow.update
    async def release(self, req: SlotRequest) -> bool:
        """Free a lease or withdraw a waiting request. Returns whether anything was removed."""
        rid = req.request_id
        found = self._leases.pop(rid, None) is not None
        if rid in self._waiting:
            self._waiting.remove(rid)
            found = True
        self._dirty = True
        return found

    @release.validator
    def _validate_release(self, req: SlotRequest) -> None:
        if not req.request_id or len(req.request_id) > MAX_REQUEST_ID:
            raise ValueError("invalid request id")
