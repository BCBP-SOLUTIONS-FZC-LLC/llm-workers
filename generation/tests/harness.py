"""Shared harness: a Temporal time-skipping server, both workers, and a fake API service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fake_engine import FakeDb, FakeEngineApi, make_input
from llm_workers_generation.activities.build import build_activities
from llm_workers_generation.queues import control_queue
from llm_workers_generation.versioning.deployment import deployment_config
from llm_workers_generation.workflows import WORKFLOWS
from temporalio.api.workflowservice.v1 import SetWorkerDeploymentCurrentVersionRequest
from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

QUEUE = "llm-generation"
BUILD_ID = "test-build"


@dataclass
class Stack:
    env: WorkflowEnvironment
    client: Client
    db: FakeDb
    api: FakeEngineApi

    async def start(
        self, workflow: str = "GenerationWorkflow", wf_id: str | None = None, **over: Any
    ) -> tuple[WorkflowHandle[Any, Any], dict[str, Any]]:
        raw = make_input(**over)
        wf_id = wf_id or raw["artefact_id"]
        handle = await self.client.start_workflow(workflow, raw, id=wf_id, task_queue=QUEUE)
        return handle, raw

    # -- the API service's side of W-3, W-4, W-5, W-7 (writes the row, then signals) ----------
    async def wait_status(self, status: str, timeout: float = 20) -> None:
        async with asyncio.timeout(timeout):
            while self.db.status != status:
                await asyncio.sleep(0.02)

    async def approve(self, handle: WorkflowHandle[Any, Any], *, signal: bool = True) -> None:
        self.db.status, self.db.outline_state, self.db.stage = "generating", "approved", "stage1"
        if signal:
            await handle.signal("approve_outline", {"outline_version": self.db.outline_version})

    async def revise(
        self,
        handle: WorkflowHandle[Any, Any],
        sections: list[tuple[str, str]],
        *,
        signal: bool = True,
    ) -> None:
        self.db.status, self.db.outline_state = "outline_building", "revising"
        self.db.outline_version = (self.db.outline_version or 1) + 1
        if signal:
            await handle.signal(
                "revise_outline",
                {
                    "outline_version": self.db.outline_version,
                    "sections": [{"ref": r, "type": t} for r, t in sections],
                },
            )

    async def cancel(self, handle: WorkflowHandle[Any, Any], *, signal: bool = True) -> None:
        self.db.status = "cancelled"
        if signal:
            await handle.signal("cancel", {"reason": "user"})

    async def resume(self, handle: WorkflowHandle[Any, Any], *, signal: bool = True) -> None:
        self.db.status, self.db.failure_reason, self.db.suspended_at = "generating", None, None
        if signal:
            await handle.signal("resume")


@asynccontextmanager
async def _serve(
    env: WorkflowEnvironment, db: FakeDb, queue: str, *, versioned: bool
) -> AsyncIterator[Stack]:
    """Both workers on ``env``. ``versioned`` gives them the deployment config ``worker.py`` uses."""
    api = FakeEngineApi(db)
    work, control = build_activities(api, env.client)
    extra: dict[str, Any] = {}
    if versioned:
        extra["deployment_config"] = deployment_config(queue, BUILD_ID)
    async with (
        Worker(
            env.client,
            task_queue=queue,
            workflows=WORKFLOWS,
            activities=work,
            max_concurrent_activities=8,
            **extra,
        ),
        Worker(
            env.client,
            task_queue=control_queue(queue),
            activities=control,
            max_concurrent_activities=16,
            **extra,
        ),
    ):
        if versioned:
            await make_current(env.client, queue)
        yield Stack(env, env.client, db, api)


@asynccontextmanager
async def stack(
    db: FakeDb | None = None, *, queue: str = QUEUE, converter: Any = None
) -> AsyncIterator[Stack]:
    """The time-skipping server: for tests that need to jump hours (reconcile, leases, deadlines).

    Do not use it where a cancel can meet an activity that is about to run out of retries: it
    skips the retry backoff, so the activity's last failure and the cancel arrive together and
    the server answers the cancel command with ``ACTIVITY_UNKNOWN`` (see ``local_stack``).
    """
    env = (
        await WorkflowEnvironment.start_time_skipping(data_converter=converter)
        if converter
        else await WorkflowEnvironment.start_time_skipping()
    )
    try:
        async with _serve(env, db or FakeDb(), queue, versioned=False) as s:
            yield s
    finally:
        await env.shutdown()


@asynccontextmanager
async def local_stack(db: FakeDb | None = None, *, queue: str = QUEUE) -> AsyncIterator[Stack]:
    """The real clock on a dev server, with the worker versioning ``worker.py`` sets up.

    The one place for that setup: both workers carry ``versioning.deployment.deployment_config``
    (the same call ``worker.py`` makes), and the build is made the deployment's current version,
    which the workflows' PINNED behaviour needs (the server refuses a pinned workflow without it).
    Retries and backoff run in real time (a three-attempt activity takes about 6 s), so use it only
    for the tests that cancel children while their activities retry. Not retried, not skipped.
    """
    env = await WorkflowEnvironment.start_local()
    try:
        async with _serve(env, db or FakeDb(), queue, versioned=True) as s:
            yield s
    finally:
        await env.shutdown()


async def make_current(client: Client, deployment: str, build_id: str = BUILD_ID) -> None:
    """Make ``build_id`` the deployment's current version once its pollers have registered."""
    request = SetWorkerDeploymentCurrentVersionRequest(
        namespace=client.namespace,
        deployment_name=deployment,
        build_id=build_id,
        ignore_missing_task_queues=True,
        allow_no_pollers=True,
    )
    async with asyncio.timeout(30):
        while True:
            try:
                await client.workflow_service.set_worker_deployment_current_version(request)
                return
            except RPCError:  # the version is not known to the server yet
                await asyncio.sleep(0.2)


async def slots(s: Stack, tenant: str, limit: int = 10, lease: int = 2100):  # noqa: ANN201
    """``(handle, acquire)`` for a tenant's ``TenantSlots``: update-with-start, one operation per call."""
    from llm_workers_generation.activities.slots import slots_workflow_id
    from llm_workers_generation.types import SlotRequest, TenantSlotsInput
    from llm_workers_generation.workflows import TenantSlots
    from temporalio.client import WithStartWorkflowOperation, WorkflowUpdateStage
    from temporalio.common import WorkflowIDConflictPolicy

    wf_id = slots_workflow_id(QUEUE, tenant)

    async def acquire(rid: str):  # noqa: ANN202
        start = WithStartWorkflowOperation(
            TenantSlots.run,
            TenantSlotsInput(QUEUE, tenant, limit, lease),
            id=wf_id,
            task_queue=QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return await s.client.start_update_with_start_workflow(
            TenantSlots.acquire,
            SlotRequest(rid),
            start_workflow_operation=start,
            wait_for_stage=WorkflowUpdateStage.ACCEPTED,
        )

    return s.client.get_workflow_handle(wf_id), acquire
