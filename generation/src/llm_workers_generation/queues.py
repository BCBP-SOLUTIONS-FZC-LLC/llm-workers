"""The six work queues, their control queues and the selector (LLD §5.3, WQ-1..8).

``task_queue_for(kind, plan, residency_mode)`` is the one function that names a queue, used by
every start, restart and resume path (WQ-3). Strict wins over plan, enterprise next, else
standard (WP-10). ``ENTERPRISE_PLANS`` mirrors the engine's constant (WQ-1); a contract test
compares the two where the engine is installed.

Replay safety: pure. Security: a strict run can never land on a shared queue (WQ-2).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

ENTERPRISE_PLANS = frozenset({"enterprise"})
CONTROL_SUFFIX = "-control"


class Kind(StrEnum):
    """What a queue carries."""

    GENERATION = "generation"
    EXTRACTION = "extraction"


class Tier(StrEnum):
    """The queue's tenant class."""

    STANDARD = "standard"
    ENTERPRISE = "enterprise"
    STRICT = "strict"


class PlanRefused(Exception):  # noqa: N818
    """The plan is missing or unknown (H-9). Raised by a validation hook, never by default."""

    def __init__(self) -> None:
        super().__init__("plan_refused")


PlanValidator = Callable[[str | None], None]
"""A hook that raises ``PlanRefused``. Not wired until IA-4 confirms the plan strings (WQ-7)."""

_QUEUES: dict[tuple[Kind, Tier], str] = {
    (Kind.GENERATION, Tier.STANDARD): "llm-generation",
    (Kind.GENERATION, Tier.ENTERPRISE): "llm-generation-enterprise",
    (Kind.GENERATION, Tier.STRICT): "llm-generation-strict",
    (Kind.EXTRACTION, Tier.STANDARD): "llm-extraction",
    (Kind.EXTRACTION, Tier.ENTERPRISE): "llm-extraction-enterprise",
    (Kind.EXTRACTION, Tier.STRICT): "llm-extraction-strict",
}
WORK_QUEUES: tuple[str, ...] = tuple(_QUEUES.values())
CONTROL_QUEUES: tuple[str, ...] = tuple(q + CONTROL_SUFFIX for q in WORK_QUEUES)


def tier_for(plan: str | None, residency_mode: str) -> Tier:
    """Strict where residency is not ``standard``, else enterprise by plan, else standard."""
    if residency_mode != "standard":
        return Tier.STRICT
    if plan is not None and plan.lower() in ENTERPRISE_PLANS:
        return Tier.ENTERPRISE
    return Tier.STANDARD


def tier_of_queue(work_queue: str) -> Tier:
    """The tier of a work queue name (``llm-extraction-strict`` is strict): a bounded label."""
    name = work_queue.removesuffix(CONTROL_SUFFIX)
    if name.endswith("-strict"):
        return Tier.STRICT
    return Tier.ENTERPRISE if name.endswith("-enterprise") else Tier.STANDARD


def queue_name(kind: Kind | str, tier: Tier | str) -> str:
    """The work queue of a kind and tier."""
    return _QUEUES[(Kind(kind), Tier(tier))]


def task_queue_for(
    kind: Kind | str,
    plan: str | None,
    residency_mode: str,
    *,
    plan_validator: PlanValidator | None = None,
) -> str:
    """The work queue for a run (§5.3). ``plan_validator`` is the unknown-plan hook (WQ-7)."""
    if plan_validator is not None:
        plan_validator(plan)
    return queue_name(kind, tier_for(plan, residency_mode))


def control_queue(work_queue: str) -> str:
    """The control queue of a work queue (§12.3). Only workflows name it."""
    return work_queue if work_queue.endswith(CONTROL_SUFFIX) else work_queue + CONTROL_SUFFIX


def kind_of_run(run_kind: str) -> Kind:
    """Map the run input's ``kind`` (``document`` or ``extraction``) to a queue kind."""
    return Kind.EXTRACTION if run_kind == "extraction" else Kind.GENERATION
