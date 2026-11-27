"""The engine as the activities see it: a port over ``rag.engine``'s public API (LLD §4.2).

Hexagonal boundary. ``RagEngineApi`` (``engine_api.py``) is the production adapter and the test
suite's ``FakeEngineApi`` is the other. Every method takes the ``RunContext`` ``run_scope`` yielded
as its first argument, as the engine's functions do. Nothing here names a database, S3 or Qdrant
type (WD-3). The helper methods at the end read the pinned pack and the content store through the
engine (``DomainProfile`` fields, ``load_section_text``, engine 1.1.0, ADR-0003).
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol
from uuid import UUID

Ctx = Any
"""The engine's ``RunContext``: opaque to this repository."""


class EngineApi(Protocol):
    """The allowed functions of ``rag.engine`` plus the pack and content helpers."""

    services: Any

    def scope(self, run_input: dict[str, Any]) -> AbstractAsyncContextManager[Ctx]:
        """``run_scope(services, run_input)``: sets context, holds no connection."""

    async def reresolve(self, ctx: Ctx) -> Any: ...
    async def run_state(self, ctx: Ctx) -> Any: ...
    async def begin_stage(self, ctx: Ctx, stage: str) -> bool: ...
    async def load_pack(self, ctx: Ctx) -> Any: ...
    async def save_outline(self, ctx: Ctx, entries: list[Any], *, gated: bool) -> Any: ...
    async def begin_section(self, ctx: Ctx, section_id: UUID) -> bool: ...
    async def save_section(self, ctx: Ctx, section_id: UUID, draft: Any) -> Any: ...
    async def save_section_gap(self, ctx: Ctx, section_id: UUID, gap: Any) -> Any: ...
    async def fail_section(self, ctx: Ctx, section_id: UUID, reason: str) -> Any: ...
    async def retrieve(
        self, ctx: Ctx, query: str, *, section_type: str | None, limit: int | None
    ) -> Any: ...
    def compose_prompt(self, **kwargs: Any) -> Any: ...
    async def call_model(
        self,
        ctx: Ctx,
        task: str,
        prompt: Any,
        *,
        section_ref: str,
        attempt: int,
        deadline_seconds: float,
    ) -> Any: ...
    async def verify(
        self,
        ctx: Ctx,
        request: Any,
        *,
        prompt: Any,
        deadline_seconds: float,
        section_ref: str,
        attempt: int,
    ) -> Any: ...
    async def refine_instruction(self, ctx: Ctx, **kwargs: Any) -> Any: ...
    async def record_facts(self, ctx: Ctx, facts: list[Any]) -> int: ...
    async def record_coherence(self, ctx: Ctx, verdict: str, conflicts: list[Any]) -> Any: ...
    async def record_checkpoint(
        self, ctx: Ctx, section_id: UUID, activity_run_id: str, nodes: list[tuple[str, int, int]]
    ) -> None: ...
    async def mint_document_version(self, ctx: Ctx) -> Any: ...
    async def run_extraction(self, ctx: Ctx) -> Any: ...
    async def suspend_for_quota(self, ctx: Ctx) -> bool: ...
    async def complete_run(self, ctx: Ctx, minted: Any) -> bool: ...
    async def fail_run(self, ctx: Ctx, reason: str, stage: str | None) -> bool: ...
    def translate(self, exc: Exception) -> Exception: ...

    # -- the pack, the section text and the exported types (engine 1.1.0, ADR-0003) ---------------
    def outline_entries(self, bundle: Any, capability: str) -> list[Any]:
        """The pack's outline for a capability, as ``OutlineEntry`` items."""

    def fact_keys(self, bundle: Any) -> tuple[str, ...]:
        """The pack's document-wide fact keys."""

    async def section_text(self, ctx: Ctx, section_id: UUID) -> str:
        """A written section's text, in memory only (WB-5)."""

    def chunk_view(self, chunk: Any) -> Any:
        """A retrieved candidate as a prompt chunk."""

    def evidence_view(self, chunk: Any) -> Any:
        """A retrieved candidate as verification evidence."""

    def verify_request(self, **kwargs: Any) -> Any:
        """A verification request over the given text and evidence."""

    def section_draft(self, **kwargs: Any) -> Any:
        """A ``SectionDraft``."""

    def gap_draft(self, **kwargs: Any) -> Any:
        """A ``GapDraft``."""

    def fact_draft(self, **kwargs: Any) -> Any:
        """A ``FactDraft``."""

    def conflict_draft(self, **kwargs: Any) -> Any:
        """A ``ConflictDraft``."""
