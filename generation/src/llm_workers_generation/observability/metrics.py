"""The workers' **workflow-side** metrics, on Temporal's ``MetricMeter`` (LLD §15.1).

Workflow code runs in Temporal's sandbox (WD-1) and may not import ``platform-fastapicommon``, so a
metric that can only be recorded *inside* a workflow stays here. Temporal's meter is replay-aware
(``workflow.metric_meter()``, suppressed on replay) and emits through the SDK runtime's Prometheus
endpoint (``METRICS_BIND_ADDRESS``, ``observability/setup.py``), not fastapicommon's ``/metrics``.

Every other metric is recorded from an activity or the worker process and lives in
``observability/app_metrics.py`` on fastapicommon. The split, with the reason each stayed:

* ``workflow_started_total``, ``workflow_completed_total``, ``workflow_duration_seconds``,
  ``workflow_history_events_total``: the start and the end of a workflow, including the
  cancelled and gone paths where no activity runs; the history length exists only inside the
  workflow. (The history length is a counter of events, not a histogram: a histogram must end
  ``_seconds`` under the platform's naming rule.)
* ``fanout_in_flight``: the document's own fan-out counter, internal to the workflow.
* ``reconcile_recovered_total``, ``run_deadline_total``, ``poison_breaker_total``: decisions the
  workflow takes from its own state; no activity runs at that moment.
* ``unrecorded_failures_total``: the workflow gives up after the terminal write's activity has
  failed for its whole window; no activity can know it was the last.

Labels come from bounded vocabularies: **no tenant, user or document id is ever a label** (OBS-2,
WS-7); ``_attrs`` rejects any other label name.

Replay safety: counters and gauges are SDK-side effects that are suppressed on replay.
"""

from __future__ import annotations

from typing import Any

from temporalio.common import MetricMeter

PREFIX = "llm_workers_"
ALLOWED_LABELS = frozenset(
    {
        "kind",
        "status",
        "activity",
        "error_type",
        "stage",
        "result",
        "scope",
        "tier",
        "phase",
        "wait",
        "capability",
    }
)


def _attrs(**labels: str) -> dict[str, str]:
    bad = set(labels) - ALLOWED_LABELS
    if bad:
        raise ValueError(f"label not allowed: {sorted(bad)}")  # a name, never a value
    return labels


class Metrics:
    """The workflow-side instruments. Cheap to build; build one per workflow run."""

    def __init__(self, meter: MetricMeter) -> None:
        def counter(name: str, desc: str) -> Any:
            return meter.create_counter(PREFIX + name, desc)

        def histogram(name: str, desc: str, unit: str = "s") -> Any:
            return meter.create_histogram_float(PREFIX + name, desc, unit)

        def gauge(name: str, desc: str) -> Any:
            return meter.create_gauge(PREFIX + name, desc)

        self.workflow_started = counter("workflow_started_total", "Workflows started")
        self.workflow_completed = counter("workflow_completed_total", "Workflows ended")
        self.workflow_duration = histogram("workflow_duration_seconds", "Start to end")
        self.history_events = counter(
            "workflow_history_events_total", "Events in the histories of ended workflows"
        )
        self.fanout_in_flight = gauge("fanout_in_flight", "Running sections")
        self.reconcile_recovered = counter("reconcile_recovered_total", "Lost signals recovered")
        self.run_deadline = counter("run_deadline_total", "Runs failed by RUN_MAX_SECONDS")
        self.poison_breaker = counter("poison_breaker_total", "Runs stopped by the breaker")
        self.unrecorded_failures = counter(
            "unrecorded_failures_total", "Terminal write not recorded"
        )

    def started(self, kind: str) -> None:
        """A workflow started."""
        self.workflow_started.add(1, _attrs(kind=kind))

    def completed(self, kind: str, status: str, seconds: float, history_events: int) -> None:
        """A workflow ended ``complete``, ``failed``, ``cancelled`` or ``gone``."""
        self.workflow_completed.add(1, _attrs(kind=kind, status=status))
        self.workflow_duration.record(seconds, _attrs(kind=kind))
        self.history_events.add(history_events, _attrs(kind=kind))

    def recovered(self, wait: str) -> None:
        """A wake found a state change whose signal never arrived."""
        self.reconcile_recovered.add(1, _attrs(wait=wait))

    def breaker(self, error_type: str) -> None:
        """The poison-section breaker fired."""
        self.poison_breaker.add(1, _attrs(error_type=error_type))

    def fanout(self, scope: str, in_flight: int) -> None:
        """Sections running now."""
        self.fanout_in_flight.set(in_flight, _attrs(scope=scope))
