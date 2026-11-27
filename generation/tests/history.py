"""Helpers that look inside a workflow history for a planted string (LLD WS-9)."""

from __future__ import annotations

import base64
from typing import Any

from google.protobuf.json_format import MessageToDict
from temporalio.client import WorkflowHandle, WorkflowHistory


def _walk(node: Any, needle: bytes, decode: bool) -> bool:
    if isinstance(node, dict):
        return any(_walk(v, needle, decode) for v in node.values())
    if isinstance(node, list):
        return any(_walk(v, needle, decode) for v in node)
    if isinstance(node, str):
        if needle.decode() in node:
            return True
        if decode:
            try:
                return needle in base64.b64decode(node, validate=True)
            except ValueError:
                return False
    return False


def history_contains(history: WorkflowHistory, needle: str, *, decode: bool = True) -> bool:
    """Does any event carry ``needle`` in clear text or inside a base64 payload?"""
    return any(_walk(MessageToDict(e), needle.encode(), decode) for e in history.events)


async def all_histories_contain(handles: list[WorkflowHandle[Any, Any]], needle: str) -> bool:
    return any(history_contains(await h.fetch_history(), needle) for h in handles)


def child_ids(history: WorkflowHistory) -> list[str]:
    """Ids of the child workflows the parent started (the test server has no visibility listing)."""
    ids = []
    for e in history.events:
        attrs = e.child_workflow_execution_started_event_attributes
        if attrs.workflow_execution.workflow_id:
            ids.append(attrs.workflow_execution.workflow_id)
    return ids


async def family(
    client,
    handle: WorkflowHandle[Any, Any],
    tenant_id: str | None = None,
    queue: str = "llm-generation",
) -> list[WorkflowHistory]:  # noqa: ANN001
    """The run's own history, every section child's and (if it ran) the tenant's slots entity."""
    parent = await handle.fetch_history()
    out = [parent] + [
        await client.get_workflow_handle(i).fetch_history() for i in child_ids(parent)
    ]
    if tenant_id:
        try:
            out.append(
                await client.get_workflow_handle(f"slots:{queue}:{tenant_id}").fetch_history()
            )
        except Exception:  # noqa: BLE001 - no slots entity ran
            pass
    return out
