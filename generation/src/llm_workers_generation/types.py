"""Activity requests and results that are not already in ``llm_models.workers`` (LLD §4.3, §7.2).

Standard library plus the plain types only, so a workflow can import it inside the sandbox. Every
field is an identifier, an enum, a number or a timestamp. **No field holds generated text, chunk
text, a prompt, a failure message or a reviewer's instruction** (ASYNC-12, WP-9). Quota, a gone
run and a terminal run are *results* (flags), never exceptions (§4.4).

Replay safety: frozen data, no behaviour. Security: the absence of any text field is the control;
``tests/security`` scans every field type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llm_models.workers import (
    ExtractionResult,
    MintedVersion,
    OutlineSaved,
    RunState,
)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """The run's wire input, for the activities that need nothing else."""

    run_input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StageRequest:
    """``begin_stage``: ``outline``, ``stage1``, ``stage2``, ``coherence`` or ``extract``."""

    run_input: dict[str, Any]
    stage: str


@dataclass(frozen=True, slots=True)
class OutlineRequest:
    """``outline``. ``revised`` holds the reviewer's edited ``(ref, type)`` pairs, never content."""

    run_input: dict[str, Any]
    revised: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class FactsRequest:
    """``capture_facts`` over the foundation sections, by id."""

    run_input: dict[str, Any]
    section_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteRequest:
    """``complete_run`` with the version ``mint_document_version`` returned."""

    run_input: dict[str, Any]
    minted: MintedVersion


@dataclass(frozen=True, slots=True)
class FailRequest:
    """``fail_run``: a reason from the sibling enum and the stage it failed at."""

    run_input: dict[str, Any]
    reason: str
    stage: str | None = None


@dataclass(frozen=True, slots=True)
class FailSectionRequest:
    """``fail_section``: close one section ``failed`` after its attempts."""

    run_input: dict[str, Any]
    section_id: str
    reason: str
    stage: str = "unknown"
    """The section's stage (a metric label)."""


@dataclass(frozen=True, slots=True)
class SlotAcquire:
    """``acquire_slot``/``release_slot``: the tenant's slot for one section child."""

    run_input: dict[str, Any]
    queue: str
    request_id: str
    waited_seconds: float | None = None
    """``release_slot`` only: workflow time waited for the slot; ``None`` if never granted."""


@dataclass(frozen=True, slots=True)
class StepResult:
    """The result of a run-state write or a count-returning step."""

    ok: bool = True
    count: int = 0
    skipped_terminal: bool = False
    gone: bool = False
    quota_suspended: bool = False


@dataclass(frozen=True, slots=True)
class RunStateResult:
    """``run_state``. ``state`` is ``None`` where the run's row is gone."""

    state: RunState | None = None
    gone: bool = False


@dataclass(frozen=True, slots=True)
class ScopeCheck:
    """``check_scope``: counts only. ``ScopeRevoked`` is an ``ApplicationError``."""

    collections: int = 0
    narrowed: bool = False
    skipped_terminal: bool = False
    gone: bool = False


@dataclass(frozen=True, slots=True)
class OutlineStep:
    """``outline``. ``saved`` is ``None`` where the run is gone or terminal."""

    saved: OutlineSaved | None = None
    skipped_terminal: bool = False
    gone: bool = False


@dataclass(frozen=True, slots=True)
class MintStep:
    """``mint_document_version``."""

    minted: MintedVersion | None = None
    skipped_terminal: bool = False
    gone: bool = False


@dataclass(frozen=True, slots=True)
class CoherenceStep:
    """``coherence_pass``."""

    verdict: str = ""
    conflict_count: int = 0
    quota_suspended: bool = False
    skipped_terminal: bool = False
    gone: bool = False


@dataclass(frozen=True, slots=True)
class ExtractionStep:
    """``run_extraction``."""

    result: ExtractionResult | None = None
    quota_suspended: bool = False
    skipped_terminal: bool = False
    gone: bool = False


@dataclass(frozen=True, slots=True)
class SlotLease:
    """A grant of one of the tenant's slots. ``granted`` is false when the wait timed out."""

    request_id: str
    granted: bool
    expires_at: float = 0.0
    """Workflow time (epoch seconds) at which the reaper frees the lease."""


@dataclass(frozen=True, slots=True)
class SlotRequest:
    """The ``acquire`` update's argument."""

    request_id: str


@dataclass(frozen=True, slots=True)
class TenantSlotsInput:
    """Start (and continue-as-new) argument of ``TenantSlots`` (WDT-3: config arrives in input)."""

    queue: str
    tenant_id: str
    limit: int
    lease_seconds: int
    leases: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    """Live ``(request_id, expiry)`` pairs carried across continue-as-new."""
    waiting: tuple[str, ...] = field(default_factory=tuple)
    """Queued request ids, in order, carried so a re-acquire keeps its place."""
