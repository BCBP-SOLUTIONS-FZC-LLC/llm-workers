"""Logging, metrics, tracing and the Temporal runtime for a worker process (LLD §15.1, §15.2).

Two Prometheus endpoints exist, by design (ADR-0006):

* the **ops server** (``observability/ops.py``, ``HEALTH_PORT``) serves ``/metrics`` from
  ``platform-fastapicommon``: the activity-side ``llm_workers_*`` metrics and ``platform_*``;
* the Temporal ``Runtime`` below serves the **SDK's own telemetry** (backlog, schedule-to-start,
  task
  failures) and the workflow-side ``llm_workers_*`` metrics (``observability/metrics.py``) on
  ``METRICS_BIND_ADDRESS``. This is a named exception to "metrics only through fastapicommon":
  workflow code cannot import it and the SDK emits its own metrics itself. Tracing uses the
SDK's OpenTelemetry interceptor, which carries the trace context through every activity and child
workflow, so one trace covers the API request, the workflow and each activity. Spans hold
identifiers and counts, never content (WS-7).

Logging and the tracer provider come from ``platform-fastapicommon`` (``configure_logging``,
``init_tracing``), the same setup the engine logs through, so the process has one log format.
Workflow code is excluded: it keeps the Temporal SDK's own workflow logger (sandbox, WD-1).
"""

from __future__ import annotations

from typing import Any

from fastapicommon.logging import configure_logging
from fastapicommon.metrics import MetricsOptions, init_metrics
from fastapicommon.metrics.registry import default_registry
from fastapicommon.telemetry import TracingConfig, init_tracing, shutdown_tracing
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from temporalio.worker import Interceptor

from llm_workers_generation.config import Settings
from llm_workers_generation.observability.app_metrics import app_metrics

SERVICE_NAME = "llm-workers"

#: Seconds-valued workflow durations: the SDK's default histogram buckets are tuned for
#: milliseconds (50 to 10000), so a duration in seconds would land in the first bucket.
WORKFLOW_DURATION_BUCKETS = (1.0, 5.0, 15.0, 60.0, 300.0, 900.0, 1800.0, 3600.0, 14400.0)


def platform_labels(settings: Settings) -> dict[str, str]:
    """The platform's three constant labels, from the one place both endpoints read them.

    ``init_metrics`` stamps them on every fastapicommon series (naming rule 8) and the Temporal
    runtime below stamps the same three on every series it serves (the SDK's own and the
    workflow-side ``llm_workers_*``), so no instrumentation code can omit or misspell them.

    ``environment`` is normalised (trimmed, lowercased) and checked against the registry's
    vocabulary here, exactly as ``init_metrics`` does, so ``APP_ENV=Prod`` is ``prod`` on both
    endpoints and ``production`` fails the start with the same error on either path.
    """
    return {
        "domain": settings.observability_domain,
        "service": SERVICE_NAME,
        "environment": default_registry().normalize_environment(settings.app_env),
    }


def build_runtime(settings: Settings) -> Runtime:
    """The process runtime. Metrics are off when ``ENABLE_METRICS`` is false."""
    if not settings.enable_metrics:
        return Runtime(telemetry=TelemetryConfig())
    return Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(
                bind_address=settings.metrics_bind_address,
                histogram_bucket_overrides={
                    "llm_workers_workflow_duration_seconds": WORKFLOW_DURATION_BUCKETS
                },
            ),
            global_tags=platform_labels(settings),
        )
    )


def build_interceptors(settings: Settings) -> list[Interceptor]:
    """The OpenTelemetry tracing interceptor where an exporter endpoint is configured."""
    if not settings.otel_exporter_otlp_endpoint:
        return []
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor()]


def init_observability(settings: Settings, *, registry: Any = None) -> None:
    """Platform logging always; platform metrics when enabled; a tracer provider where configured.

    The tracer provider is installed only where an exporter endpoint is set.

    ``configure_logging`` installs fastapicommon's handler on the stdlib root logger, so the
    ``temporalio`` Python loggers (SDK, activity and workflow loggers) come out in the same format.
    The Temporal core's own (Rust) logs do not pass through it.

    ``init_metrics`` validates the identity against the platform registry: domain ``llm``, service
    ``llm-workers`` (so Tier 3 names are ``llm_workers_*``) and ``APP_ENV`` one of ``local``,
    ``dev``, ``test``, ``staging``, ``prod``; anything else fails the process at start. ``registry``
    is for tests (a private collector registry).
    """
    configure_logging(level=settings.log_level.lower(), environment=settings.app_env)
    if settings.enable_metrics:
        init_metrics(
            MetricsOptions(
                service_name=SERVICE_NAME,
                build_version=settings.build_id,
                domain=platform_labels(settings)["domain"],
                environment=platform_labels(settings)["environment"],
            ),
            registry=registry,
        )
    app_metrics()  # built now (once per registry), so a bad name fails the start, not an activity
    if settings.otel_exporter_otlp_endpoint:
        init_tracing(
            TracingConfig.from_env(
                service_name=SERVICE_NAME,
                service_version=settings.build_id,
                domain=settings.observability_domain,
                environment=settings.app_env,
                endpoint=settings.otel_exporter_otlp_endpoint,
            )
        )


def shutdown_observability() -> None:
    """Flush and stop the tracer provider at process shutdown (a no-op if none was installed)."""
    shutdown_tracing()
