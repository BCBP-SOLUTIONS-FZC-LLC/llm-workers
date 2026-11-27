import pytest
from llm_workers_generation.queues import (
    CONTROL_QUEUES,
    WORK_QUEUES,
    PlanRefused,
    Tier,
    control_queue,
    kind_of_run,
    task_queue_for,
    tier_for,
)


def test_exactly_six_work_queues_and_a_control_queue_each() -> None:
    assert set(WORK_QUEUES) == {
        "llm-generation",
        "llm-generation-enterprise",
        "llm-generation-strict",
        "llm-extraction",
        "llm-extraction-enterprise",
        "llm-extraction-strict",
    }
    assert CONTROL_QUEUES == tuple(q + "-control" for q in WORK_QUEUES)


@pytest.mark.parametrize(
    ("kind", "plan", "residency", "expected"),
    [
        ("generation", "starter", "standard", "llm-generation"),
        ("generation", "pro", "standard", "llm-generation"),
        ("generation", "enterprise", "standard", "llm-generation-enterprise"),
        ("generation", "ENTERPRISE", "standard", "llm-generation-enterprise"),
        ("generation", None, "standard", "llm-generation"),
        ("generation", "unknown-plan", "standard", "llm-generation"),  # no error today (WQ-6)
        ("generation", "enterprise", "strict", "llm-generation-strict"),  # strict wins
        ("generation", "starter", "strict", "llm-generation-strict"),
        ("generation", "pro", "eu-only", "llm-generation-strict"),  # anything but standard
        ("extraction", "pro", "standard", "llm-extraction"),
        ("extraction", "enterprise", "standard", "llm-extraction-enterprise"),
        ("extraction", "enterprise", "strict", "llm-extraction-strict"),
    ],
)
def test_selector(kind: str, plan: str | None, residency: str, expected: str) -> None:
    assert task_queue_for(kind, plan, residency) == expected


def test_strict_never_lands_on_a_shared_queue() -> None:
    for plan in (None, "pro", "enterprise"):
        assert task_queue_for("generation", plan, "strict").endswith("-strict")
        assert tier_for(plan, "strict") is Tier.STRICT


def test_plan_validation_hook_can_refuse() -> None:
    def refuse(plan: str | None) -> None:
        if plan not in {"starter", "pro", "enterprise"}:
            raise PlanRefused

    assert (
        task_queue_for("generation", "pro", "standard", plan_validator=refuse) == "llm-generation"
    )
    with pytest.raises(PlanRefused):
        task_queue_for("generation", None, "standard", plan_validator=refuse)


def test_control_queue_and_kind() -> None:
    assert control_queue("llm-extraction") == "llm-extraction-control"
    assert control_queue("llm-extraction-control") == "llm-extraction-control"
    assert kind_of_run("extraction").value == "extraction"
    assert kind_of_run("document").value == "generation"


def test_enterprise_plans_match_the_engine_where_installed() -> None:
    ids = pytest.importorskip("rag.domain.workflows.ids")
    from llm_workers_generation.queues import ENTERPRISE_PLANS

    assert ENTERPRISE_PLANS == ids.ENTERPRISE_PLANS  # WQ-1: one definition of "enterprise"
