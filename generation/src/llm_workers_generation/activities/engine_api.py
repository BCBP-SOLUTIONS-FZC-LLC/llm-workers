"""The production adapter: ``rag.engine`` behind the ``EngineApi`` port (LLD §4, WB-1).

This is the **one module** that imports the engine. It imports ``rag.engine`` (the public API)
and, for ``build_rag_engine``, ``rag.infrastructure.config.Settings`` (the type ``build_engine``
takes; the single internal import, allowed by the architecture test and listed in ADR-0003).

Engine 1.1.0 closes ADR-0003 gaps 1 to 5, so this adapter reads the real fields and calls the real
functions: ``DomainProfile.outline_skeletons`` (values are tuples of ``SkeletonSection``) and
``DomainProfile.fact_keys``; ``rag.engine.load_section_text``; and the exported prompt, evidence and
verification types (``PromptChunk``, ``EvidenceChunk``, ``VerificationRequest``). The two pack keys
are an **assumed Tender contract** (the pack JSON names are this repository's proposal; Tender has
not confirmed them). Gap 6 (the engine's metrics identity, 1.2.0) is open: see ``build_rag_engine``.

``EngineApiGap`` stays for a missing engine addition (today none is needed at run time).
``PackOutlineMissing`` is a pack with no outline for the run's capability (a content problem,
not retryable).

Security: nothing here logs or returns content.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

import rag.engine as engine

ERRORS = engine  # the engine's typed errors: engine.QuotaRefused, engine.RunGone, ...


class EngineApiGap(Exception):  # noqa: N818
    """The installed engine lacks an addition this repository needs (ADR-0003; gap 6 is 1.2.0)."""

    def __init__(self) -> None:
        super().__init__("engine_api_gap")


class PackOutlineMissing(Exception):  # noqa: N818
    """The pinned pack has no outline for the run's capability (an empty ``outline_skeletons``)."""

    def __init__(self) -> None:
        super().__init__("pack_outline_missing")


class RagEngineApi:
    """``EngineApi`` over the real engine."""

    def __init__(self, services: Any) -> None:
        self.services = services

    def scope(self, run_input: dict[str, Any]) -> AbstractAsyncContextManager[Any]:
        """The activity's scope."""
        return engine.run_scope(self.services, run_input)

    async def reresolve(self, ctx: Any) -> Any:
        """Re-read access and re-resolve scope."""
        return await engine.reresolve(ctx)

    async def run_state(self, ctx: Any) -> Any:
        """The run's state."""
        return await engine.run_state(ctx)

    async def begin_stage(self, ctx: Any, stage: str) -> bool:
        """Begin a stage."""
        return await engine.begin_stage(ctx, stage)

    async def load_pack(self, ctx: Any) -> Any:
        """The pinned pack."""
        return await engine.load_pack(ctx)

    async def save_outline(self, ctx: Any, entries: list[Any], *, gated: bool) -> Any:
        """Save the outline."""
        return await engine.save_outline(ctx, entries, gated=gated)

    async def begin_section(self, ctx: Any, section_id: UUID) -> bool:
        """Begin a section."""
        return await engine.begin_section(ctx, section_id)

    async def save_section(self, ctx: Any, section_id: UUID, draft: Any) -> Any:
        """Save a finished section."""
        return await engine.save_section(ctx, section_id, draft)

    async def save_section_gap(self, ctx: Any, section_id: UUID, gap: Any) -> Any:
        """Save a section as a gap."""
        return await engine.save_section_gap(ctx, section_id, gap)

    async def fail_section(self, ctx: Any, section_id: UUID, reason: str) -> Any:
        """Close a section failed."""
        return await engine.fail_section(ctx, section_id, reason)

    async def retrieve(
        self, ctx: Any, query: str, *, section_type: str | None, limit: int | None
    ) -> Any:
        """Search."""
        return await engine.retrieve(ctx, query, section_type=section_type, limit=limit)

    def compose_prompt(self, **kwargs: Any) -> Any:
        """Compose the prompt."""
        return engine.compose_prompt(**kwargs)

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
        """One model call."""
        return await engine.call_model(
            ctx,
            task,
            prompt,
            section_ref=section_ref,
            attempt=attempt,
            deadline_seconds=deadline_seconds,
        )

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
        """The verification ladder."""
        return await engine.verify(
            ctx,
            request,
            prompt=prompt,
            deadline_seconds=deadline_seconds,
            section_ref=section_ref,
            attempt=attempt,
        )

    async def refine_instruction(self, ctx: Any, **kwargs: Any) -> Any:
        """The Instruction Refiner."""
        return await engine.refine_instruction(ctx, **kwargs)

    async def record_facts(self, ctx: Any, facts: list[Any]) -> int:
        """Write the facts."""
        return await engine.record_facts(ctx, facts)

    async def record_coherence(self, ctx: Any, verdict: str, conflicts: list[Any]) -> Any:
        """Write the verdict."""
        return await engine.record_coherence(ctx, verdict, conflicts)

    async def record_checkpoint(
        self, ctx: Any, section_id: UUID, activity_run_id: str, nodes: list[tuple[str, int, int]]
    ) -> None:
        """Write the inspection row."""
        await engine.record_checkpoint(ctx, section_id, activity_run_id, nodes)

    async def mint_document_version(self, ctx: Any) -> Any:
        """Mint the document version."""
        return await engine.mint_document_version(ctx)

    async def run_extraction(self, ctx: Any) -> Any:
        """Run the extraction."""
        return await engine.run_extraction(ctx)

    async def suspend_for_quota(self, ctx: Any) -> bool:
        """Suspend the run."""
        return await engine.suspend_for_quota(ctx)

    async def complete_run(self, ctx: Any, minted: Any) -> bool:
        """Complete the run."""
        return await engine.complete_run(ctx, minted)

    async def fail_run(self, ctx: Any, reason: str, stage: str | None) -> bool:
        """Fail the run."""
        return await engine.fail_run(ctx, reason, stage)

    def translate(self, exc: Exception) -> Exception:
        """Map a domain or Gateway error to the engine's typed error."""
        return engine.translate(exc)

    # -- the pack and the section text (engine 1.1.0, ADR-0003 gaps 1 to 3) ---------------------
    def outline_entries(self, bundle: Any, capability: str) -> list[Any]:
        """The pack's outline for ``capability``. A pack without one is ``PackOutlineMissing``."""
        skeleton = bundle.domain_profile.outline_skeletons.get(capability)
        if not skeleton:
            raise PackOutlineMissing
        return [
            engine.OutlineEntry(
                ref=sec.ref, section_type=sec.section_type, is_foundation=sec.is_foundation
            )
            for sec in skeleton
        ]

    def fact_keys(self, bundle: Any) -> tuple[str, ...]:
        """The pack's fact keys. An empty tuple is a pack with none."""
        return tuple(bundle.domain_profile.fact_keys)

    async def section_text(self, ctx: Any, section_id: UUID) -> str:
        """A section's current text, in memory only (``RunGone`` or ``ContentUnavailable``)."""
        return await engine.load_section_text(ctx, section_id)

    def chunk_view(self, chunk: Any) -> engine.PromptChunk:
        """A candidate (or a section's text) as the exported ``PromptChunk``."""
        return engine.PromptChunk(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            document_title=chunk.document_title,
            heading_path=chunk.heading_path,
            clause_ref=chunk.clause_ref,
            document_role=chunk.document_role,
            document_id=chunk.document_id,
            document_version=chunk.document_version,
            language=chunk.language,
            excerpt=chunk.excerpt,
        )

    def evidence_view(self, chunk: Any) -> engine.EvidenceChunk:
        """A candidate as the exported ``EvidenceChunk``."""
        return engine.EvidenceChunk(chunk_id=chunk.chunk_id, text=chunk.text)

    def verify_request(self, **kwargs: Any) -> engine.VerificationRequest:
        """The exported ``VerificationRequest``."""
        return engine.VerificationRequest(**kwargs)

    def section_draft(self, **kwargs: Any) -> Any:
        """A ``SectionDraft``."""
        return engine.SectionDraft(**kwargs)

    def gap_draft(self, **kwargs: Any) -> Any:
        """A ``GapDraft``."""
        return engine.GapDraft(**kwargs)

    def fact_draft(self, **kwargs: Any) -> Any:
        """A ``FactDraft``."""
        return engine.FactDraft(**kwargs)

    def conflict_draft(self, **kwargs: Any) -> Any:
        """A ``ConflictDraft``."""
        return engine.ConflictDraft(**kwargs)


async def build_rag_engine() -> tuple[RagEngineApi, Any]:
    """Build the engine once at worker start-up; returns the adapter and the closable ``Engine``."""
    from rag.infrastructure.config import Settings as RagSettings  # the one internal import

    # The engine's own Tier 3 metrics are named llm_retrieval_generation_*, and fastapicommon allows
    # a process one identity: under this service's (llm-workers) they raise ValueError at build
    # time. The engine's metrics are therefore off in a worker (logs and traces stay on) until the
    # platform or the engine settles one identity for a process that runs both (ADR-0006).
    built = await engine.build_engine(RagSettings(enable_metrics=False))
    return RagEngineApi(built.services), built
