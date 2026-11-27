"""``SectionSteps`` over the engine: the building blocks one section calls (LLD §7.3, LW-7).

Everything the section reads or writes goes through ``EngineApi`` (WA-7). Chunks, prompts and
drafts live on this object, in memory, for the life of one activity, and never in a result, a
heartbeat, a checkpoint or a log (WB-5, WA-11).

Assumptions: the retrieval query is built from the section's type and reference and the pack's
vocabulary (the pack declares retrieval parameters per section type, §6.7); a "deeper" retrieval
doubles the rerank window (the sibling LLD's widened retry, D-16). See ADR-0003 for the engine
helpers used.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from llm_models.workers import SectionOutcome, SectionRef

from llm_workers_generation.activities.ports import Ctx, EngineApi

DRAFT_DEADLINE_SECONDS = 240.0
VERIFY_DEADLINE_SECONDS = 240.0


class EngineSectionSteps:
    """One section's steps."""

    def __init__(self, api: EngineApi, ctx: Ctx, ref: SectionRef, regeneration: bool) -> None:
        self._api, self._ctx, self._ref, self._regen = api, ctx, ref, regeneration
        self._bundle: Any = None
        self._profile: Any = None
        self._narrowed = False
        self._retrieved: Any = None
        self._refined = ""
        self._prompt: Any = None
        self._answer: Any = None
        self._verification: Any = None
        self.attempt_tokens: list[int] = []
        """Tokens per drafted attempt: the last is kept, the rest are waste (WA-10)."""
        self._label = ref.ref or ref.section_id

    @property
    def tokens(self) -> tuple[int, int]:
        """``(total, retried)`` tokens for the metrics."""
        return sum(self.attempt_tokens), sum(self.attempt_tokens[:-1])

    async def _pack(self) -> Any:
        if self._bundle is None:
            self._bundle = await self._api.load_pack(self._ctx)
            self._profile, _ = self._bundle.domain_profile.profile_for(self._ref.section_type)
        return self._bundle

    async def reresolve(self) -> None:
        """WP-4: re-read access and re-resolve scope before any corpus read."""
        self._narrowed = bool((await self._api.reresolve(self._ctx)).narrowed)

    async def retrieve(self, *, deeper: bool) -> bool:
        """Search; the second, deeper pass doubles the rerank window and adds the vocabulary."""
        await self._pack()
        query = f"{self._ref.section_type} {self._ref.ref or ''}".strip()
        if deeper:
            query = " ".join([query, *self._profile.vocabulary])
        limit = self._profile.rerank_out * (2 if deeper else 1)
        self._retrieved = await self._api.retrieve(
            self._ctx, query, section_type=self._ref.section_type, limit=limit
        )
        return str(self._retrieved.outcome.verdict) == "sufficient"

    def insufficient_reason(self) -> str:
        """A fixed code, never text."""
        return str(self._retrieved.outcome.verdict_reason or "insufficient_evidence")

    async def refine(self) -> None:
        """The Instruction Refiner, per section, for a regeneration (D-13). A no-op otherwise."""
        if not self._regen:
            return
        bundle = await self._pack()
        text = await self._api.section_text(self._ctx, UUID(self._ref.section_id))
        run = await self._api.refine_instruction(
            self._ctx,
            section_text=text,
            section_type=self._ref.section_type,
            mandatory_clauses=sorted(self._profile.mandatory_clauses),
            facts=(),
            bundle=bundle,
        )
        self._refined = "" if run is None else str(run.result.refined_instruction)

    async def compose(self, attempt: int) -> None:
        """The six-layer prompt over the retrieved chunks."""
        bundle = await self._pack()
        task = f"Write section {self._label} ({self._ref.section_type})."
        self._prompt = self._api.compose_prompt(
            task="draft",
            skeleton=self._profile.skeleton,
            tenant_overrides=bundle.domain_profile.tenant_overrides,
            user_context="",
            chunks=[self._api.chunk_view(c) for c in self._retrieved.outcome.chunks],
            user_prompt=(task + " " + self._refined).strip(),
        )

    async def draft(self, attempt: int) -> None:
        """Draft through the Gateway, with ``x-correlation-id = run:section:attempt`` (WA-19)."""
        self._answer = await self._api.call_model(
            self._ctx,
            "draft",
            self._prompt,
            section_ref=self._label,
            attempt=attempt,
            deadline_seconds=DRAFT_DEADLINE_SECONDS,
        )
        self.attempt_tokens.append(
            int(self._answer.prompt_tokens) + int(self._answer.completion_tokens)
        )

    async def verify(self, attempt: int) -> bool:
        """The ladder. Returns whether it asks for a retry."""
        request = self._api.verify_request(
            text=self._answer.text,
            evidence=tuple(self._api.evidence_view(c) for c in self._retrieved.outcome.chunks),
            refs=dict(self._prompt.refs),
            mandatory_clauses=tuple(sorted(self._profile.mandatory_clauses)),
            compliance=bool(self._profile.compliance),
            section_label=self._ref.section_type,
        )
        self._verification = await self._api.verify(
            self._ctx,
            request,
            prompt=self._prompt,
            deadline_seconds=VERIFY_DEADLINE_SECONDS,
            section_ref=self._label,
            attempt=attempt,
        )
        return str(self._verification.disposition) == "retry"

    async def save(self, retries: int) -> SectionOutcome:
        """Write the version (S3, then one transaction: WT-2)."""
        draft = self._api.section_draft(
            content=self._answer.text.encode(),
            retrieval_set_id=self._retrieved.retrieval_set_id,
            prompt_hash=self._prompt.prompt_hash,
            model_profile_id=self._answer.model,
            serving_tier=self._answer.serving_tier,
            verification=self._verification,
            markers=tuple(self._verification.markers),
            scope_narrowed=self._narrowed,
            auto_retry_count=retries,
        )
        outcome: SectionOutcome = await self._api.save_section(
            self._ctx, UUID(self._ref.section_id), draft
        )
        return outcome

    async def save_gap(self, reason: str) -> SectionOutcome:
        """End the section as a gap: a reason code, no content."""
        gap = self._api.gap_draft(reason=reason)
        outcome: SectionOutcome = await self._api.save_section_gap(
            self._ctx, UUID(self._ref.section_id), gap
        )
        return outcome
