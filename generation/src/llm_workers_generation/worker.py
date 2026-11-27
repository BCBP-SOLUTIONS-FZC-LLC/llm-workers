"""Worker bootstrap: one process, one work queue, one control queue (LLD §3.4, §12.3, §13.5).

``main`` reads settings (fail fast), builds the engine once, connects to Temporal with the
client certificate and the encrypting data converter, and runs two workers on one client:

* the **work worker**: all four workflows and the long activities on ``<queue>``;
* the **control worker**: activity-only, on ``<queue>-control``, with its own slots, so a run-state
  write is never queued behind a ten-minute section.

On ``SIGTERM`` both stop polling and in-flight activities get ``WORKER_SHUTDOWN_GRACE_SECONDS``
(§13.5). Worker Deployments pin the build (WV-5). Readiness is "connected and polling" (§17.1),
served by the ops server (``observability/ops.py``), which stops last of all.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta
from typing import Any

from fastapicommon.logging import get_logger
from temporalio.client import Client
from temporalio.converter import DataConverter
from temporalio.service import TLSConfig
from temporalio.worker import Worker

from llm_workers_generation.activities.build import build_activities
from llm_workers_generation.activities.engine_api import build_rag_engine
from llm_workers_generation.codec.failure import build_data_converter
from llm_workers_generation.codec.payload_codec import StaticKeyProvider
from llm_workers_generation.config import Settings
from llm_workers_generation.observability.ops import Readiness, start_ops
from llm_workers_generation.observability.setup import (
    build_interceptors,
    build_runtime,
    init_observability,
    shutdown_observability,
)
from llm_workers_generation.versioning.deployment import deployment_config
from llm_workers_generation.workflows import WORKFLOWS

log = get_logger(__name__)


def _tls(settings: Settings) -> TLSConfig | bool:
    """Client-certificate TLS from mounted files; the system store is trusted (WE-3). No API key."""
    if not settings.temporal_tls_cert_path or not settings.temporal_tls_key_path:
        return False
    with (
        open(settings.temporal_tls_cert_path, "rb") as cert,
        open(settings.temporal_tls_key_path, "rb") as key,
    ):
        return TLSConfig(client_cert=cert.read(), client_private_key=key.read())


async def connect(settings: Settings) -> Client:
    """Connect with the data converter (payload codec and failure converter, WS-12, WS-13)."""
    converter = DataConverter.default
    if settings.payload_codec_key_id and settings.payload_codec_key_path:
        keys = StaticKeyProvider.from_file(
            settings.payload_codec_key_id, settings.payload_codec_key_path.get_secret_value()
        )
        converter = build_data_converter(keys)
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace or "default",
        tls=_tls(settings),
        runtime=build_runtime(settings),
        data_converter=converter,
    )


async def run(
    settings: Settings, stop: asyncio.Event | None = None, client: Client | None = None
) -> None:
    """Run both workers until ``stop`` is set (``SIGTERM``/``SIGINT`` when none is given)."""
    init_observability(settings)
    if settings.fleet_cap_disagrees:
        log.warning("fanout_global_disagrees_with_fleet_cap", fleet_cap=settings.fleet_cap)
    client = client or await connect(settings)
    api, built = await build_rag_engine()
    work_acts, control_acts = build_activities(api, client)
    grace = timedelta(seconds=settings.worker_shutdown_grace_seconds)
    common: dict[str, Any] = {
        "client": client,
        "graceful_shutdown_timeout": grace,
        "interceptors": build_interceptors(settings),
    }
    work = Worker(
        task_queue=settings.work_queue,
        workflows=WORKFLOWS,
        activities=work_acts,
        max_concurrent_activities=settings.max_concurrent_activities,
        max_concurrent_workflow_tasks=settings.max_concurrent_workflow_tasks,
        deployment_config=deployment_config(settings.deployment_name, settings.build_id),
        **common,
    )
    control = Worker(
        task_queue=settings.control_queue,
        activities=control_acts,
        max_concurrent_activities=settings.max_concurrent_control_activities,
        # the same deployment version: a pinned run's control activities run on its build (WV-5)
        deployment_config=deployment_config(settings.deployment_name, settings.build_id),
        **common,
    )
    if stop is None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    readiness = Readiness()
    # one task in this loop; /ready is 503 until both workers poll
    ops = await start_ops(readiness, settings.health_port)
    try:
        async with work, control:  # polling starts here
            readiness.ready = True
            log.info("polling", queue=settings.work_queue, ops_port=ops.port)
            await stop.wait()
            readiness.ready = False  # leave rotation first, then drain within the grace period
            log.info("draining", grace_seconds=settings.worker_shutdown_grace_seconds)
        log.info("drained")  # leaving the block waited for in-flight activities (§13.5)
    finally:
        await ops.stop()  # last, so /live and /metrics answer for the whole drain
        log.info("ops_stopped")
        await built.aclose()
        log.info("engine_closed")
        shutdown_observability()


def main() -> None:
    """Console entry point."""
    asyncio.run(run(Settings()))  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
