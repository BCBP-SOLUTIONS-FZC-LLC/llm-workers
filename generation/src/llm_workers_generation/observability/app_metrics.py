"""The workers' activity-side metrics, on ``platform-fastapicommon`` (LLD §15.1, WD-5).

Tier 3 collectors (``new_counter``, ``new_histogram``) named ``llm_workers_*``: the prefix is
``<domain>_<service>_`` for domain ``llm`` and service ``llm-workers``, which fastapicommon checks
at construction. They sit on the registry the ops server's ``/metrics`` serves, next to the
``platform_*`` collectors, with ``domain``, ``service`` and ``environment`` added as constant
labels.

Needs ``init_metrics`` first (``observability/setup.init_observability`` does it). Where metrics are
disabled (``ENABLE_METRICS=false``) every method is a no-op.

Rules the library enforces and this module follows:

* counters end ``_total``, histograms end ``_seconds``, gauges never end ``_total``;
* no label named like an id (``tenant_id``, ``user_id`` ...). ``_attrs`` also restricts labels to a
  bounded allow-list, so a tenant, user or document value can never become a label (OBS-2, WS-7).

Because histograms must end ``_seconds``, a token histogram cannot exist: tokens are the counter
``section_tokens_total`` (sum per stage and tier), and cost per document is not built (no price
source in the worker). Workflow-side metrics are in ``observability/metrics.py``, on Temporal's
meter.

Not imported by workflow code (WD-1).
"""

from __future__ import annotations

import weakref
from typing import Any

from fastapicommon.metrics import (
    metrics_registry,
    new_counter,
    new_histogram,
    platform_metrics_enabled,
)

from llm_workers_generation.observability.metrics import PREFIX, _attrs

# Sections and slot waits last seconds to minutes: the registry's request-duration buckets end at
# seconds, so these histograms carry their own.
LONG_BUCKETS = (0.5, 1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800)
ACTIVITY_BUCKETS = (0.01, 0.05, 0.25, 1, 5, 15, 30, 60, 120, 300, 600)


class AppMetrics:
    """The activity-side instruments. Build once per process (``app_metrics()``)."""

    def __init__(self) -> None:
        self.enabled = platform_metrics_enabled()
        if not self.enabled:
            return
        self.activity_duration = new_histogram(
            PREFIX + "activity_duration_seconds",
            "One activity attempt, start to end",
            ["activity"],
            buckets=ACTIVITY_BUCKETS,
        )
        self.activity_failures = new_counter(
            PREFIX + "activity_failures_total",
            "Activity attempts that failed, by error type",
            ["activity", "error_type"],
        )
        self.section_duration = new_histogram(
            PREFIX + "section_duration_seconds",
            "One write_section attempt, start to end",
            ["stage"],
            buckets=LONG_BUCKETS,
        )
        self.section_outcome = new_counter(
            PREFIX + "section_outcome_total",
            "How a section attempt ended",
            ["stage", "result"],
        )
        self.slot_wait = new_histogram(
            PREFIX + "slot_wait_seconds",
            "Time a section waited for its tenant's slot",
            ["tier"],
            buckets=LONG_BUCKETS,
        )
        self.quota_suspensions = new_counter(
            PREFIX + "quota_suspensions_total",
            "Runs suspended on quota, by the stage they were in",
            ["stage"],
        )
        self.run_gone = new_counter(
            PREFIX + "run_gone_total",
            "Activity calls that found the run's rows erased",
            ["activity"],
        )
        self.gate_expired = new_counter(
            PREFIX + "gate_expired_total", "Runs failed by the outline gate's timer"
        )
        self.scope_revoked = new_counter(
            PREFIX + "scope_revoked_total", "Runs failed by an empty scope"
        )
        self.section_tokens = new_counter(
            PREFIX + "section_tokens_total",
            "Tokens drafted for sections (every attempt)",
            ["stage", "tier"],
        )
        self.retried_tokens = new_counter(
            PREFIX + "retried_tokens_total",
            "Tokens spent on an attempt that was repeated (waste)",
            ["tier"],
        )

    def activity(self, name: str, seconds: float) -> None:
        """One activity attempt's duration."""
        if self.enabled:
            self.activity_duration.labels(**_attrs(activity=name)).observe(seconds)

    def activity_failed(self, name: str, error_type: str) -> None:
        """One activity attempt failed with ``error_type`` (§4.4)."""
        if self.enabled:
            self.activity_failures.labels(**_attrs(activity=name, error_type=error_type)).inc()

    def section(self, stage: str, outcome: str, seconds: float) -> None:
        """One ``write_section`` attempt ended."""
        if self.enabled:
            self.section_outcome.labels(**_attrs(stage=stage, result=outcome)).inc()
            self.section_duration.labels(**_attrs(stage=stage)).observe(seconds)

    def section_closed_failed(self, stage: str) -> None:
        """``fail_section`` closed a section after its attempts."""
        if self.enabled:
            self.section_outcome.labels(**_attrs(stage=stage, result="failed")).inc()

    def slot_waited(self, tier: str, seconds: float) -> None:
        """A section waited ``seconds`` for a tenant slot."""
        if self.enabled:
            self.slot_wait.labels(**_attrs(tier=tier)).observe(seconds)

    def quota_suspended(self, stage: str) -> None:
        """A run was suspended on quota while in ``stage``."""
        if self.enabled:
            self.quota_suspensions.labels(**_attrs(stage=stage)).inc()

    def gone(self, activity: str) -> None:
        """An activity found the run's rows erased (RunGone)."""
        if self.enabled:
            self.run_gone.labels(**_attrs(activity=activity)).inc()

    def failed_run(self, reason: str) -> None:
        """``fail_run`` ended a run with ``reason``: count the two reasons that have a metric."""
        if not self.enabled:
            return
        if reason == "gate_expired":
            self.gate_expired.inc()
        elif reason == "scope_revoked":
            self.scope_revoked.inc()

    def tokens(self, stage: str, tier: str, total: int, retried: int) -> None:
        """Tokens of one section; ``retried`` is the waste of WA-10."""
        if self.enabled:
            self.section_tokens.labels(**_attrs(stage=stage, tier=tier)).inc(total)
            if retried:
                self.retried_tokens.labels(**_attrs(tier=tier)).inc(retried)


# One instrument set per collector registry: fastapicommon registers each metric on the registry
# ``/metrics`` serves, and a second registration of a name there is a ``DuplicateTimeseries``.
_by_registry: weakref.WeakKeyDictionary[Any, AppMetrics] = weakref.WeakKeyDictionary()
_disabled: AppMetrics | None = None


def app_metrics() -> AppMetrics:
    """The process's instruments for the active registry: built once per registry, then reused.

    ``init_observability`` calls this at start, so a bad name fails the start, not an activity.
    Where metrics are not initialised (``ENABLE_METRICS=false``) it returns a no-op instance.
    """
    global _disabled
    if not platform_metrics_enabled():
        if _disabled is None:
            _disabled = AppMetrics()
        return _disabled
    registry = metrics_registry()
    existing = _by_registry.get(registry)
    if existing is None:
        existing = _by_registry[registry] = AppMetrics()
    return existing
