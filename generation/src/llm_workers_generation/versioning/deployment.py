"""Worker Deployment configuration (LLD §5.5, WV-5, WV-6).

Each work queue is one Worker Deployment named for the queue, and the build id is the image tag
(it carries the engine tag the build pins). The default behaviour is *pinned*; the two workflows
that wait for weeks (``GenerationWorkflow``, ``TenantSlots``) declare auto-upgrade on their own
``@workflow.defn``. If the server does not support Worker Deployments (PL-6 open) the fallback is
build-id sets with the same markers: ``use_worker_versioning=False`` and ``build_id`` on the worker.
"""

from __future__ import annotations

from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import WorkerDeploymentConfig


def deployment_config(queue: str, build_id: str) -> WorkerDeploymentConfig:
    """The worker's deployment: name = queue, default behaviour pinned."""
    return WorkerDeploymentConfig(
        version=WorkerDeploymentVersion(deployment_name=queue, build_id=build_id),
        use_worker_versioning=True,
        default_versioning_behavior=VersioningBehavior.PINNED,
    )
