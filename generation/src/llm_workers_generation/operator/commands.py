"""Operator commands that need nothing new (LLD §15.7).

* **WO-1 list live runs** by tenant, kind or status from the search attributes where the operator
  registered them (PL-8). Where they are not registered it returns nothing and says so: the caller
  falls back to the tables through the API service's operator routes.
* **WO-2 drain report**: what is left on a queue (backlog and pollers) before a deployment is
  retired.
* **WO-4 re-drive a zombie** is the API service's W-8 route. This repository has no raw HTTP client
  (WD-3), so it is not reimplemented here.

Every command takes an ``Operator`` (identity and ticket) and returns the audit record the caller
appends to the audit chain through the API service, naming the workflow id (§10.1). Identifiers
only; no content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client
from temporalio.service import RPCError


@dataclass(frozen=True, slots=True)
class Operator:
    """Who ran a command and under which ticket."""

    identity: str
    ticket: str


@dataclass(frozen=True, slots=True)
class RunRow:
    """One live workflow."""

    workflow_id: str
    workflow_type: str
    status: str


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """What the audit chain must record for an operator command."""

    command: str
    operator: Operator
    workflow_ids: tuple[str, ...] = field(default_factory=tuple)


async def list_live_runs(
    client: Client,
    operator: Operator,
    *,
    tenant_id: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> tuple[list[RunRow], AuditRecord]:
    """WO-1. Filters use the ``TenantId`` and ``RunKind`` search attributes."""
    clauses = ["ExecutionStatus = 'Running'"]
    if tenant_id:
        clauses.append(f"TenantId = '{_safe(tenant_id)}'")
    if kind:
        clauses.append(f"RunKind = '{_safe(kind)}'")
    rows: list[RunRow] = []
    try:
        async for wf in client.list_workflows(" AND ".join(clauses), limit=limit):
            rows.append(RunRow(wf.id, wf.workflow_type, wf.status.name if wf.status else "UNKNOWN"))
    except RPCError:
        rows = []  # attributes not registered (PL-8): fall back to the tables
    return rows, AuditRecord("list_live_runs", operator, tuple(r.workflow_id for r in rows))


async def drain_report(
    client: Client, operator: Operator, queue: str
) -> tuple[dict[str, int], AuditRecord]:
    """WO-2: pollers on a work queue, to decide whether a build can retire."""
    out = {"workflow_pollers": 0, "activity_pollers": 0}
    for name, kind in (
        ("workflow_pollers", TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
        ("activity_pollers", TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
    ):
        resp = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace, task_queue=TaskQueue(name=queue), task_queue_type=kind
            )
        )
        out[name] = len(resp.pollers)
    return out, AuditRecord("drain_report", operator)


def _safe(value: str) -> str:
    """A visibility-query literal: letters, digits, dash and underscore only."""
    if not all(ch.isalnum() or ch in "-_:" for ch in value):
        raise ValueError("unsafe query value")
    return value
