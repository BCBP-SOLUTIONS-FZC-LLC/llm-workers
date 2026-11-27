"""The ops server: ``/health``, ``/live``, ``/ready`` and ``/metrics`` on one port (§15.1, §17.1).

A small FastAPI app that mounts ``platform-fastapicommon``'s health router (one readiness check)
and its metrics router, served by ``uvicorn.Server.serve()`` as **one task in the worker's event
loop**: no thread, no second process.

* ``/health`` and ``/live``: the event loop answers.
* ``/ready``: ``Readiness.ready`` is true while the Temporal connection is up and both workers are
  polling. It is cleared first when the worker is told to stop, so the pod leaves rotation while the
  drain runs (LLD §13.5); ``/live`` keeps answering until the process exits.
* ``/metrics``: the workers' own metrics (``observability/app_metrics.py``) and the platform's
  ``platform_*`` collectors, from fastapicommon's registry. Temporal's SDK telemetry is a separate
  endpoint (``METRICS_BIND_ADDRESS``, ``observability/setup.py``).

uvicorn's ``Server.serve`` replaces the process's SIGTERM and SIGINT handlers with its own and
re-raises
the signal when it exits. That would bypass the worker's graceful stop, so ``capture_signals`` is
overridden to do nothing: the worker owns the signals (pinned by the SIGTERM test in
``tests/integration/test_worker_bootstrap.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI
from fastapicommon.health import create_health_router
from fastapicommon.metrics import metrics_router


@dataclass(slots=True)
class Readiness:
    """What ``/ready`` reports: set once both workers poll, cleared when the stop begins."""

    ready: bool = False


def build_ops_app(readiness: Readiness) -> FastAPI:
    """The ops app: fastapicommon's health router (one readiness check) and metrics router."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_health_router([lambda: readiness.ready]))
    app.include_router(metrics_router)
    return app


class OpsServer(uvicorn.Server):
    """A uvicorn server that leaves the process's signal handlers to the worker."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        """No-op: the worker's own SIGTERM/SIGINT handlers stay in place."""
        yield


@dataclass(slots=True)
class RunningOps:
    """A started ops server and the task running it."""

    server: OpsServer
    task: asyncio.Task[None]

    @property
    def port(self) -> int:
        """The bound port (useful when ``port=0``)."""
        return int(self.server.servers[0].sockets[0].getsockname()[1])

    async def stop(self) -> None:
        """Ask the server to exit and wait for it. Safe to call twice."""
        self.server.should_exit = True
        await self.task


class OpsServerError(RuntimeError):
    """The ops server could not start (the port is taken, say)."""


async def _serve(server: OpsServer) -> None:
    """``server.serve()``, with uvicorn's ``sys.exit(3)`` on a failed bind turned into an error.

    A ``SystemExit`` inside a task would tear down the event loop; an exception lets ``run``
    tidy up.
    """
    try:
        await server.serve()
    except SystemExit:
        raise OpsServerError("the ops server could not bind its port") from None


async def start_ops(readiness: Readiness, port: int, host: str = "0.0.0.0") -> RunningOps:  # noqa: S104
    """Start the ops server as a task in the running loop and return once it accepts connections.

    ``log_config=None`` leaves uvicorn's loggers on the stdlib root logger, which fastapicommon has
    configured, so they come out in the platform format; the access log is off (probe traffic).
    """
    config = uvicorn.Config(
        build_ops_app(readiness),
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        lifespan="off",
        timeout_graceful_shutdown=5,
    )
    server = OpsServer(config)
    task = asyncio.create_task(_serve(server))
    while not server.started:
        if task.done():  # the bind failed: surface the error instead of waiting forever
            await task  # raises OpsServerError on a failed bind
            raise OpsServerError("the ops server stopped before it started")
        await asyncio.sleep(0.01)
    return RunningOps(server, task)
