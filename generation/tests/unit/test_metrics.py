"""The 25 LLD metrics: where each lives (LLD §15.1, WD-5)."""

import pytest
from fastapicommon.metrics import MetricsNotInitializedError
from llm_workers_generation.observability import app_metrics as am
from llm_workers_generation.observability.metrics import ALLOWED_LABELS, PREFIX, Metrics, _attrs
from temporalio.common import MetricMeter

# workflow-side: Temporal's meter (workflow code cannot import fastapicommon)
WORKFLOW_SIDE = {
    "workflow_started_total",
    "workflow_completed_total",
    "workflow_duration_seconds",
    "workflow_history_events_total",
    "fanout_in_flight",
    "reconcile_recovered_total",
    "run_deadline_total",
    "poison_breaker_total",
    "unrecorded_failures_total",
}
# activity- and process-side: fastapicommon
ACTIVITY_SIDE = {
    "activity_duration_seconds",
    "activity_failures_total",
    "section_duration_seconds",
    "section_outcome_total",
    "slot_wait_seconds",
    "quota_suspensions_total",
    "run_gone_total",
    "gate_expired_total",
    "scope_revoked_total",
    "section_tokens_total",  # replaces tokens_per_section: a histogram must end in _seconds
    "retried_tokens_total",
}
# declared in the LLD, built nowhere: there is no event that yields a truthful value (see the tracker)
NOT_BUILT = {
    "runs_suspended",
    "runs_awaiting_gate",
    "tenants_waiting",
    "load_shed_steps",
    "cost_per_document",
}


def test_every_label_comes_from_the_bounded_vocabulary() -> None:
    assert not {"tenant_id", "tenant", "user", "user_id", "document_id", "run_id"} & ALLOWED_LABELS
    with pytest.raises(ValueError):
        _attrs(tenant_id="x")


class RecordingMeter:
    def __init__(self) -> None:
        self.names: list[str] = []

    def _make(self, name: str, *a: object) -> object:
        self.names.append(name)
        return MetricMeter.noop.create_counter(name)

    create_counter = create_gauge = create_histogram_float = _make


def test_the_workflow_side_instruments_are_exactly_the_listed_ones() -> None:
    meter = RecordingMeter()
    Metrics(meter)  # type: ignore[arg-type]
    assert {
        n.removeprefix(PREFIX) for n in meter.names
    } == WORKFLOW_SIDE and PREFIX == "llm_workers_"


def test_the_activity_side_instruments_are_exactly_the_listed_ones(app_registry) -> None:  # noqa: ANN001
    am.app_metrics()  # the instruments are built on first use
    names = {m.name for m in app_registry.collect() if m.name.startswith(PREFIX)}
    # prometheus_client strips the _total of a counter family name
    assert {n.removeprefix(PREFIX) for n in names} == {
        n.removesuffix("_total") for n in ACTIVITY_SIDE
    }


def test_the_25_are_accounted_for_once_each() -> None:
    """9 on Temporal's meter, 11 on fastapicommon, 5 not built: 25 LLD instruments, each in one place."""
    assert not (
        WORKFLOW_SIDE & ACTIVITY_SIDE or WORKFLOW_SIDE & NOT_BUILT or ACTIVITY_SIDE & NOT_BUILT
    )
    assert (len(WORKFLOW_SIDE), len(ACTIVITY_SIDE), len(NOT_BUILT)) == (9, 11, 5)
    assert "tokens_per_section" not in WORKFLOW_SIDE | ACTIVITY_SIDE | NOT_BUILT


def test_fastapicommon_rejects_the_names_this_repo_must_not_use(app_registry) -> None:  # noqa: ANN001
    """The library's own rules, as they apply here: a histogram must end in _seconds, the prefix is
    llm_workers_, and an id-like label is refused."""
    from fastapicommon.metrics import new_histogram

    for bad in ("llm_workers_tokens_per_section", "llm_workers_cost_per_document"):
        with pytest.raises(ValueError, match="histograms must end in _seconds"):
            new_histogram(bad, "x", ["tier"], registry=app_registry)
    with pytest.raises(ValueError, match="llm_workers_"):
        new_histogram("llm_retrieval_generation_x_seconds", "x", registry=app_registry)
    with pytest.raises(ValueError, match="prohibited"):
        new_histogram("llm_workers_y_seconds", "y", ["tenant_id"], registry=app_registry)


def test_activity_side_values_land_on_the_registry_with_identity_labels(app_registry) -> None:  # noqa: ANN001
    m = am.app_metrics()
    m.activity("run_state", 0.1)
    m.activity_failed("write_section", "Internal")
    m.section("stage1", "complete", 2.0)
    m.section_closed_failed("stage2")
    m.slot_waited("standard", 3.0)
    m.quota_suspended("stage2")
    m.gone("run_state")
    m.failed_run("gate_expired")
    m.failed_run("scope_revoked")
    m.failed_run("internal")  # no metric for it
    m.tokens("stage1", "standard", 100, 30)
    reg = app_registry
    labels = {"domain": "llm", "service": "llm-workers", "environment": "test"}
    assert reg.get_sample_value("llm_workers_gate_expired_total", labels) == 1
    assert reg.get_sample_value("llm_workers_scope_revoked_total", labels) == 1
    assert (
        reg.get_sample_value(
            "llm_workers_section_tokens_total", {**labels, "stage": "stage1", "tier": "standard"}
        )
        == 100
    )
    assert (
        reg.get_sample_value("llm_workers_retried_tokens_total", {**labels, "tier": "standard"})
        == 30
    )
    assert (
        reg.get_sample_value(
            "llm_workers_section_outcome_total", {**labels, "stage": "stage2", "result": "failed"}
        )
        == 1
    )
    assert (
        reg.get_sample_value("llm_workers_quota_suspensions_total", {**labels, "stage": "stage2"})
        == 1
    )
    assert (
        reg.get_sample_value("llm_workers_slot_wait_seconds_count", {**labels, "tier": "standard"})
        == 1
    )


def test_activity_side_is_a_no_op_when_metrics_are_disabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(am, "platform_metrics_enabled", lambda: False)  # ENABLE_METRICS=false
    m = am.AppMetrics()
    assert not m.enabled
    m.activity("a", 1.0)
    m.activity_failed("a", "Internal")
    m.section("s", "complete", 1.0)
    m.section_closed_failed("s")
    m.slot_waited("standard", 1.0)
    m.quota_suspended("s")
    m.gone("a")
    m.failed_run("gate_expired")
    m.tokens("s", "standard", 1, 1)


def test_building_a_metric_before_initialisation_is_an_error() -> None:
    """fastapicommon refuses a collector before init_metrics: the worker initialises it first."""
    from fastapicommon.metrics import api

    with pytest.raises(MetricsNotInitializedError):
        # the library's own guard, called with no identity known
        orig = api.metrics_identity
        api.metrics_identity = lambda: None  # type: ignore[assignment]
        try:
            api.new_counter("llm_workers_x_total", "x")
        finally:
            api.metrics_identity = orig  # type: ignore[assignment]


def test_workflow_side_helpers_accept_only_enums() -> None:
    m = Metrics(MetricMeter.noop)
    m.started("document")
    m.completed("document", "complete", 1.0, 10)
    m.recovered("gate")
    m.breaker("inference_unavailable")
    m.fanout("document", 3)
