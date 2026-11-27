"""A fake ``rag.engine`` and the in-memory run it drives (LLD §18.1: "a fake Engine").

It reproduces the engine's *contract*, not its code: the typed errors, the run state machine the
workers rely on (§2.2), guarded writes that refuse a terminal run (WB-6), and idempotent
repeats (WB-8). Tests flip ``FakeDb`` fields to play the API service (approve, resume, cancel) and
to inject Gateway behaviour (quota, outage, residency).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from llm_models.workers import (
    ExtractionResult,
    GenerationWorkflowInput,
    MintedVersion,
    OutlineSaved,
    RunState,
    SectionOutcome,
    SectionRef,
)
from rag.domain.generation.run import RUN_TRANSITIONS  # the real engine's run state machine
from rag.domain.shared.enums import RunStatus

CANARY = "CANARY-7f3a9c-DO-NOT-LEAK"
ENGINE_API_VERSION = "1.1.0"


# -- the engine's typed errors (rag/engine/errors.py) ----------------------------------------------
class EngineError(Exception): ...


class RunGone(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("run_gone")


class ScopeRevoked(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("scope_revoked")


class QuotaRefused(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("quota_refused")


class ResidencyRefused(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("residency_refused")


class SchemaViolation(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("schema_violation")


class InferenceUnavailable(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("inference_unavailable")


class RunTerminal(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("run_terminal")


class NothingToMint(EngineError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__("nothing_to_mint")


class ContentUnavailable(EngineError):  # noqa: N818
    """``load_section_text`` could not read or verify a section's text (any cause, no detail)."""

    def __init__(self) -> None:
        super().__init__("content_unavailable")


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    ref: str
    section_type: str
    is_foundation: bool = False


@dataclass(frozen=True, slots=True)
class SectionDraft:
    content: bytes
    retrieval_set_id: UUID
    prompt_hash: str
    model_profile_id: str
    serving_tier: str
    verification: Any
    markers: tuple[dict[str, str], ...] = ()
    scope_narrowed: bool = False
    auto_retry_count: int = 0


@dataclass(frozen=True, slots=True)
class GapDraft:
    reason: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class FactDraft:
    fact_key: str
    value: str
    section_id: UUID


@dataclass(frozen=True, slots=True)
class ConflictDraft:
    section_id: UUID
    fact_key: str
    established_value: str
    other_value: str
    established_by_section_id: UUID


def translate(exc: Exception) -> Exception:
    return exc


def run_scope(*a: Any, **k: Any) -> Any:
    raise NotImplementedError  # the adapter binds FakeEngineApi


async def build_engine(*a: Any, **k: Any) -> Any:
    raise NotImplementedError


# -- a fake pack ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Profile:
    vocabulary: tuple[str, ...] = ("term",)
    rerank_out: int = 4
    mandatory_clauses: frozenset[str] = frozenset()
    skeleton: str = "skeleton"
    compliance: bool = False


@dataclass(frozen=True, slots=True)
class _Domain:
    outline_skeletons: dict[str, list[tuple[str, str, bool]]]
    fact_keys: tuple[str, ...] = ("contract_value",)
    tenant_overrides: str = ""

    def profile_for(self, section_type: str | None) -> tuple[_Profile, str]:
        return _Profile(), "default"


@dataclass(frozen=True, slots=True)
class _Bundle:
    domain_profile: _Domain


@dataclass(slots=True)
class FakeCtx:
    input: GenerationWorkflowInput


@dataclass
class FakeDb:
    """The run, its sections and what the fake Gateway should do."""

    status: str = "pending"
    stage: str | None = None
    outline_state: str = "none"
    outline_version: int | None = None
    gate_expires_at: str | None = None
    suspended_at: str | None = None
    failure_reason: str | None = None
    gone: bool = False
    sections: list[dict[str, Any]] = field(default_factory=list)
    skeleton: list[tuple[str, str, bool]] = field(
        default_factory=lambda: [("1", "intro", True), ("2", "scope", False), ("3", "price", False)]
    )
    fact_keys: tuple[str, ...] = ("contract_value",)
    behaviors: dict[str, list[str]] = field(default_factory=dict)
    """ref (or ``facts``/``coherence``/``extract``) -> queue of actions: quota, inference, residency, schema, scope, ok."""
    content: dict[str, str] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)
    coherence: str | None = None
    conflicts: int = 0
    minted: bool = False
    active: int = 0
    peak: int = 0
    call_delay: float = 0.0
    extraction_result: str = "complete"
    revision_sections: list[tuple[str, str]] | None = None
    gate_days: int = 30
    conflict_value: str = "100"
    checkpoints: list[Any] = field(default_factory=list)
    failed_from: str | None = None
    """The run status ``fail_run`` found (the engine 1.1.0 edges: pending, outline_building, suspended...)."""

    def count(self, name: str) -> int:
        return self.calls.get(name, 0)

    @property
    def terminal(self) -> bool:
        return self.status in ("complete", "failed", "cancelled")

    def tick(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def add_sections(self, entries: list[tuple[str, str, bool]]) -> None:
        self.sections = [
            {
                "id": str(uuid4()),
                "ref": r,
                "type": t,
                "foundation": f,
                "state": "pending",
                "version": False,
                "ordinal": i,
            }
            for i, (r, t, f) in enumerate(entries)
        ]

    def section(self, section_id: str) -> dict[str, Any]:
        return next(s for s in self.sections if s["id"] == section_id)

    def behave(self, key: str) -> str:
        queue = self.behaviors.get(key, [])
        return queue.pop(0) if queue else "ok"


class FakeEngineApi:
    """The ``EngineApi`` port over a ``FakeDb``."""

    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.services = None

    @asynccontextmanager
    async def scope(self, run_input: dict[str, Any]) -> Any:
        yield FakeCtx(GenerationWorkflowInput.from_dict(run_input))

    def translate(self, exc: Exception) -> Exception:
        return exc

    def _row(self) -> FakeDb:
        if self.db.gone:
            raise RunGone
        return self.db

    # -- run-state ----------------------------------------------------------------------------
    async def run_state(self, ctx: Any) -> RunState:
        self.db.tick("run_state")
        d = self._row()
        refs = tuple(
            SectionRef(s["id"], s["ref"], s["type"], s["foundation"], s["state"], s["ordinal"])
            for s in d.sections
        )
        return RunState(
            d.status,
            d.stage,
            d.outline_state,
            d.outline_version,
            d.gate_expires_at,
            d.suspended_at,
            d.failure_reason,
            refs,
        )

    async def reresolve(self, ctx: Any) -> Any:
        self.db.tick("reresolve")
        self._row()
        if self.db.behave("scope") == "scope":
            raise ScopeRevoked
        return type("Snap", (), {"collections": 2, "narrowed": False})()

    async def begin_stage(self, ctx: Any, stage: str) -> bool:
        self.db.tick("begin_stage")
        d = self._row()
        if d.terminal:
            return False
        if stage == "outline":
            if d.outline_state in ("none", "revising"):
                d.status, d.outline_state = "outline_building", "building"
        elif d.status == "pending":
            d.status = "generating"
        d.stage = stage
        d.events.append(f"progress:{stage}")
        return True

    async def load_pack(self, ctx: Any) -> Any:
        d = self._row()
        return _Bundle(_Domain({ctx.input.capability: d.skeleton}, d.fact_keys))

    def outline_entries(self, bundle: Any, capability: str) -> list[Any]:
        return [
            OutlineEntry(r, t, f) for r, t, f in bundle.domain_profile.outline_skeletons[capability]
        ]

    def fact_keys(self, bundle: Any) -> tuple[str, ...]:
        return tuple(bundle.domain_profile.fact_keys)

    async def save_outline(self, ctx: Any, entries: list[Any], *, gated: bool) -> OutlineSaved:
        self.db.tick("save_outline")
        d = self._row()
        if d.terminal:
            raise RunTerminal
        d.add_sections([(e.ref, e.section_type, e.is_foundation) for e in entries])
        if gated:
            d.status, d.outline_state = "awaiting_outline_approval", "ready"
            d.outline_version = d.outline_version or 1
            d.gate_expires_at = (datetime.now(UTC) + timedelta(days=d.gate_days)).isoformat()
            d.events.append("OutlineReady")
        return OutlineSaved(
            tuple(s["id"] for s in d.sections), d.outline_version, d.gate_expires_at
        )

    # -- sections -----------------------------------------------------------------------------
    async def begin_section(self, ctx: Any, section_id: UUID) -> bool:
        d = self._row()
        s = d.section(str(section_id))
        if d.terminal or (s["version"] and not ctx.input.is_regeneration):
            return False
        s["state"] = "generating"
        return True

    async def retrieve(
        self, ctx: Any, query: str, *, section_type: str | None, limit: int | None
    ) -> Any:
        self.db.tick("retrieve")
        verdict = (
            "insufficient"
            if self.db.behave(f"retrieve:{section_type}") == "insufficient"
            else "sufficient"
        )
        chunk = type(
            "C",
            (),
            {
                "chunk_id": "k1",
                "text": CANARY + " chunk",
                "document_title": "t",
                "heading_path": None,
                "clause_ref": None,
                "document_role": "x",
                "document_id": None,
                "document_version": 1,
                "language": "en",
                "excerpt": "",
            },
        )()
        outcome = type(
            "O", (), {"verdict": verdict, "verdict_reason": "no_evidence", "chunks": (chunk,)}
        )()
        return type("R", (), {"outcome": outcome, "retrieval_set_id": uuid4()})()

    def compose_prompt(self, **kwargs: Any) -> Any:
        return type(
            "P", (), {"system": "s", "user": "u", "prompt_hash": "h", "refs": {"c-01": "k1"}}
        )()

    async def call_model(
        self,
        ctx: Any,
        task: str,
        prompt: Any,
        *,
        section_ref: str,
        attempt: int,
        deadline_seconds: float,
    ) -> Any:
        d = self._row()
        d.tick(f"call_model:{section_ref}")
        action = d.behave(section_ref)
        if action == "quota":
            raise QuotaRefused
        if action == "inference":
            raise InferenceUnavailable
        if action == "residency":
            raise ResidencyRefused
        if action == "schema":
            raise SchemaViolation
        if action == "boom":
            raise RuntimeError(f"db exploded {CANARY}")
        d.active += 1
        d.peak = max(d.peak, d.active)
        try:
            if d.call_delay:
                await asyncio.sleep(d.call_delay)
        finally:
            d.active -= 1
        if task == "extract":
            if section_ref == "facts":
                text = json.dumps(
                    {
                        "facts": [
                            {"key": "contract_value", "value": "100", "section": s["id"]}
                            for s in d.sections
                            if s["foundation"]
                        ]
                    }
                )
            else:
                values = {
                    s["id"]: {"contract_value": "100" if s["ordinal"] == 0 else d.conflict_value}
                    for s in d.sections
                    if s["version"]
                }
                text = json.dumps({"values": values})
            return type(
                "A",
                (),
                {
                    "text": text,
                    "model": "m",
                    "serving_tier": "primary",
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                },
            )()
        return type(
            "A",
            (),
            {
                "text": f"{CANARY} drafted text",
                "model": "m",
                "serving_tier": "primary",
                "prompt_tokens": 10,
                "completion_tokens": 20,
            },
        )()

    async def verify(
        self,
        ctx: Any,
        request: Any,
        *,
        prompt: Any,
        deadline_seconds: float,
        section_ref: str,
        attempt: int,
    ) -> Any:
        weak = self.db.behave(f"verify:{section_ref}") == "weak"
        return type("V", (), {"disposition": "retry" if weak else "surface", "markers": ()})()

    async def refine_instruction(self, ctx: Any, **kwargs: Any) -> Any:
        return None

    async def save_section(self, ctx: Any, section_id: UUID, draft: Any) -> SectionOutcome:
        d = self._row()
        if d.terminal:
            raise RunTerminal
        s = d.section(str(section_id))
        s["state"], s["version"] = "complete", True
        d.content[s["ref"]] = draft.content.decode()
        d.tick("save_section")
        return SectionOutcome(str(section_id), "complete", version_id=str(uuid4()))

    async def save_section_gap(self, ctx: Any, section_id: UUID, gap: Any) -> SectionOutcome:
        d = self._row()
        if d.terminal:
            raise RunTerminal
        d.section(str(section_id))["state"] = "gap"
        d.tick("save_section_gap")
        return SectionOutcome(str(section_id), "gap", gap_reason=gap.reason)

    async def fail_section(self, ctx: Any, section_id: UUID, reason: str) -> Any:
        d = self._row()
        d.tick("fail_section")
        if not d.terminal:
            d.section(str(section_id))["state"] = "failed"
        return SectionOutcome(str(section_id), "failed", failure_reason=reason)

    async def record_checkpoint(
        self, ctx: Any, section_id: UUID, activity_run_id: str, nodes: list[tuple[str, int, int]]
    ) -> None:
        self.db.tick("record_checkpoint")
        self.db.checkpoints = nodes

    # -- facts, coherence, mint ---------------------------------------------------------------
    async def section_text(self, ctx: Any, section_id: UUID) -> str:
        self.db.tick("section_text")
        if self.db.behave("content") == "content":
            raise ContentUnavailable
        return CANARY + " " + self.db.section(str(section_id))["ref"]

    def chunk_view(self, chunk: Any) -> Any:
        return chunk

    def evidence_view(self, chunk: Any) -> Any:
        return chunk

    def verify_request(self, **kwargs: Any) -> Any:
        return type("VR", (), kwargs)()

    def section_draft(self, **kwargs: Any) -> Any:
        return SectionDraft(**kwargs)

    def gap_draft(self, **kwargs: Any) -> Any:
        return GapDraft(**kwargs)

    def fact_draft(self, **kwargs: Any) -> Any:
        return FactDraft(**kwargs)

    def conflict_draft(self, **kwargs: Any) -> Any:
        return ConflictDraft(**kwargs)

    async def record_facts(self, ctx: Any, facts: list[Any]) -> int:
        d = self._row()
        if d.terminal:
            raise RunTerminal
        d.facts = [(f.fact_key, f.value) for f in facts]
        return len(facts)

    async def record_coherence(self, ctx: Any, verdict: str, conflicts: list[Any]) -> Any:
        d = self._row()
        if d.terminal:
            raise RunTerminal
        d.coherence, d.conflicts = verdict, len(conflicts)
        return type("CR", (), {"verdict": verdict, "conflict_count": len(conflicts)})()

    async def mint_document_version(self, ctx: Any) -> MintedVersion:
        d = self._row()
        if d.terminal:
            raise RunTerminal
        if not any(s["version"] for s in d.sections):
            raise NothingToMint
        d.minted = True
        return MintedVersion(str(uuid4()), 1, "TIER_2", "complete")

    # -- terminal and suspension edges ----------------------------------------------------------
    async def suspend_for_quota(self, ctx: Any) -> bool:
        d = self._row()
        d.tick("suspend_for_quota")
        if d.status != "generating":
            return False
        d.status, d.failure_reason = "suspended", "quota_exhausted"
        d.suspended_at = datetime.now(UTC).isoformat()
        d.events.append("suspended")
        return True

    async def complete_run(self, ctx: Any, minted: Any) -> bool:
        d = self._row()
        d.tick("complete_run")
        if d.terminal:
            return False
        d.status = "complete"
        d.events.append("GenerationCompleted")
        return True

    async def fail_run(self, ctx: Any, reason: str, stage: str | None) -> bool:
        d = self._row()
        d.tick("fail_run")
        if d.terminal:
            return False
        if RunStatus.FAILED not in RUN_TRANSITIONS[RunStatus(d.status)]:
            raise RuntimeError("InvariantViolation RUN-7")  # the engine's own, untyped
        d.failed_from = d.status
        d.status, d.failure_reason = "failed", reason
        d.events.append("GenerationFailed")
        return True

    async def run_extraction(self, ctx: Any) -> ExtractionResult:
        d = self._row()
        d.tick("run_extraction")
        action = d.behave("extract")
        if action == "quota":
            raise QuotaRefused
        if action == "schema":
            raise SchemaViolation
        if action == "residency":
            raise ResidencyRefused
        if action == "inference":
            raise InferenceUnavailable
        if d.terminal:
            raise RunTerminal
        d.status = "complete"
        return ExtractionResult(d.extraction_result, str(uuid4()), "TIER_2")


def make_input(**over: Any) -> dict[str, Any]:
    """A run input as the API's Temporal client sends it."""
    base: dict[str, Any] = {
        "tenant_id": str(UUID(int=1)),
        "principal_id": str(UUID(int=2)),
        "artefact_id": str(uuid4()),
        "generation_run_id": str(uuid4()),
        "capability": "tender_response",
        "requested_mode": "generate",
        "residency_mode": "standard",
        "domain_profile_version": "p1",
        "language_pack_version": "l1",
        "plan": "pro",
        "kind": "document",
        "outline_gate": True,
        "origin": "original",
        "trace_id": "t",
        "correlation_id": "c",
    }
    base.update(over)
    return base
