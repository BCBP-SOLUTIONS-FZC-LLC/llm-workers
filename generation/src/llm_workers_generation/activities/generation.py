"""The activity set (LLD §7): wrappers and the orchestration steps this service owns.

Every activity: opens ``run_scope`` first (WA-1), re-resolves scope before reading the corpus
(WA-2), heartbeats from a background task when it is long (WA-14), returns identifiers only
(WA-4), is safe to repeat (WA-5, WB-8), never writes a terminal run (WA-8) and translates errors
once (WA-6, ``_guard``). A quota refusal from any model-calling activity is a *result*
(WA-17). There is no SQL here: every read and write is an ``EngineApi`` call (WD-3).

Replay safety: activities are not replayed. Security: no text in any argument, result,
heartbeat or error.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from llm_models.workers import (
    ExtractionResult,
    OutlineSaved,
    RunState,
    SectionJob,
    SectionOutcome,
)
from temporalio import activity

from llm_workers_generation import tuning
from llm_workers_generation.activities._guard import activity_metrics, run_activity
from llm_workers_generation.activities.engine_api import ERRORS
from llm_workers_generation.activities.ports import Ctx, EngineApi
from llm_workers_generation.activities.section_steps import EngineSectionSteps
from llm_workers_generation.graphs.section_graph import Trace, run_section_graph
from llm_workers_generation.types import (
    CoherenceStep,
    CompleteRequest,
    ExtractionStep,
    FactsRequest,
    FailRequest,
    FailSectionRequest,
    MintStep,
    OutlineRequest,
    OutlineStep,
    RunRequest,
    RunStateResult,
    ScopeCheck,
    StageRequest,
    StepResult,
)

R = TypeVar("R")
_QUOTA_STEP = StepResult(ok=False, quota_suspended=True)
_GONE = StepResult(ok=False, gone=True)
_TERMINAL = StepResult(ok=False, skipped_terminal=True)
_MODEL_DEADLINE_SECONDS = 240.0
_FACT_TASK = "extract"


class GenerationActivities:
    """All run activities except the slot pair (``SlotActivities``)."""

    def __init__(self, api: EngineApi) -> None:
        self._api = api

    async def _run(
        self,
        name: str,
        run_input: dict[str, Any],
        body: Callable[[Ctx], Awaitable[R]],
        *,
        gone: R,
        terminal: R,
        quota: R | None = None,
        beat: tuple[str | int, ...] | None = None,
    ) -> R:
        return await run_activity(
            self._api,
            activity_metrics(),
            name,
            run_input,
            body,
            gone=gone,
            terminal=terminal,
            quota=quota,
            beat=beat,
        )

    # -- run-state activities (control queue) -------------------------------------------------
    @activity.defn(name="run_state")
    async def run_state(self, req: RunRequest) -> RunStateResult:
        """Read the run's status, stage, outline, gate and sections."""

        async def body(ctx: Ctx) -> RunStateResult:
            state: RunState = await self._api.run_state(ctx)
            return RunStateResult(state)

        return await self._run(
            "run_state",
            req.run_input,
            body,
            gone=RunStateResult(gone=True),
            terminal=RunStateResult(gone=True),
        )

    @activity.defn(name="check_scope")
    async def check_scope(self, req: RunRequest) -> ScopeCheck:
        """Re-resolve scope at the generate level. ``ScopeRevoked`` is a non-retryable failure."""

        async def body(ctx: Ctx) -> ScopeCheck:
            snap = await self._api.reresolve(ctx)
            return ScopeCheck(int(snap.collections), bool(snap.narrowed))

        return await self._run(
            "check_scope",
            req.run_input,
            body,
            gone=ScopeCheck(gone=True),
            terminal=ScopeCheck(skipped_terminal=True),
        )

    @activity.defn(name="begin_stage")
    async def begin_stage(self, req: StageRequest) -> StepResult:
        """Write ``current_stage``, the run edge and the artefact edge (§2.2)."""

        async def body(ctx: Ctx) -> StepResult:
            ok = await self._api.begin_stage(ctx, req.stage)
            return StepResult(ok=ok, skipped_terminal=not ok)

        return await self._run("begin_stage", req.run_input, body, gone=_GONE, terminal=_TERMINAL)

    @activity.defn(name="suspend_for_quota")
    async def suspend_for_quota(self, req: RunRequest) -> StepResult:
        """``generating`` to ``suspended``, stamped, with the progress event."""

        async def body(ctx: Ctx) -> StepResult:
            state: RunState = await self._api.run_state(ctx)  # the stage it is suspended in
            suspended = await self._api.suspend_for_quota(ctx)
            if suspended:  # a real transition, once per suspension
                activity_metrics().quota_suspended(state.stage or "unknown")
            return StepResult(ok=suspended)

        return await self._run(
            "suspend_for_quota", req.run_input, body, gone=_GONE, terminal=_TERMINAL
        )

    @activity.defn(name="complete_run")
    async def complete_run(self, req: CompleteRequest) -> StepResult:
        """``generating`` to ``complete``; the event carries the failed and gap counts."""

        async def body(ctx: Ctx) -> StepResult:
            ok = await self._api.complete_run(ctx, req.minted)
            return StepResult(ok=ok, skipped_terminal=not ok)

        return await self._run("complete_run", req.run_input, body, gone=_GONE, terminal=_TERMINAL)

    @activity.defn(name="fail_run")
    async def fail_run(self, req: FailRequest) -> StepResult:
        """``failed`` with a reason and ``GenerationFailed``, once (WEV-5)."""

        async def body(ctx: Ctx) -> StepResult:
            ended = await self._api.fail_run(ctx, req.reason, req.stage)
            if ended:  # this call ended the run, so a repeat counts nothing
                activity_metrics().failed_run(req.reason)
            return StepResult(ok=ended)

        return await self._run("fail_run", req.run_input, body, gone=_GONE, terminal=_TERMINAL)

    @activity.defn(name="fail_section")
    async def fail_section(self, req: FailSectionRequest) -> StepResult:
        """Close one section ``failed`` after its attempts. Publishes no event (AOB-5)."""

        async def body(ctx: Ctx) -> StepResult:
            await self._api.fail_section(ctx, UUID(req.section_id), req.reason)
            activity_metrics().section_closed_failed(req.stage)
            return StepResult()

        return await self._run("fail_section", req.run_input, body, gone=_GONE, terminal=_TERMINAL)

    @activity.defn(name="mint_document_version")
    async def mint_document_version(self, req: RunRequest) -> MintStep:
        """Roll the sections' current versions into one document version (AV-3)."""

        async def body(ctx: Ctx) -> MintStep:
            return MintStep(await self._api.mint_document_version(ctx))

        return await self._run(
            "mint_document_version",
            req.run_input,
            body,
            gone=MintStep(gone=True),
            terminal=MintStep(skipped_terminal=True),
        )

    # -- orchestration activities (work queue) ------------------------------------------------
    @activity.defn(name="outline")
    async def outline(self, req: OutlineRequest) -> OutlineStep:
        """Build the section list from the pinned pack's skeleton (LW-9). No model call."""

        async def body(ctx: Ctx) -> OutlineStep:
            await self._api.reresolve(ctx)
            gated = bool(ctx.input.outline_gate) and not ctx.input.is_regeneration
            state: RunState = await self._api.run_state(ctx)
            bundle = await self._api.load_pack(ctx)
            from_pack = self._api.outline_entries(bundle, ctx.input.capability)
            foundation = {e.section_type: e.is_foundation for e in reversed(from_pack)}
            if req.revised is not None:  # the reviewer's edited refs and types
                entries = [
                    _entry(from_pack[0], ref, kind, foundation.get(kind, False))
                    for ref, kind in req.revised
                ]
            elif (
                state.outline_state == "revising"
            ):  # the signal was lost: the pending rows are the edit
                entries = [
                    _entry(from_pack[0], s.ref or s.section_id, s.section_type, s.is_foundation)
                    for s in state.section_refs
                ]
            else:
                entries = from_pack
            saved: OutlineSaved = await self._api.save_outline(ctx, entries, gated=gated)
            return OutlineStep(saved)

        return await self._run(
            "outline",
            req.run_input,
            body,
            gone=OutlineStep(gone=True),
            terminal=OutlineStep(skipped_terminal=True),
            beat=("outline",),
        )

    @activity.defn(name="write_section")
    async def write_section(self, job: SectionJob) -> SectionOutcome:
        """One section: ``begin_section``, the graph, ``save_section`` or ``save_section_gap``."""
        run_input = dict(job.run_input)
        sid = job.section_id
        quota = SectionOutcome(sid, "failed", quota_suspended=True)

        async def body(ctx: Ctx) -> SectionOutcome:
            state: RunState = await self._api.run_state(ctx)
            if state.status in tuning.TERMINAL_STATUSES:
                return SectionOutcome(sid, "failed", skipped_terminal=True)
            ref = next((s for s in state.section_refs if s.section_id == sid), None)
            if ref is None:
                return SectionOutcome(sid, "failed", failure_reason="internal")
            if not await self._api.begin_section(ctx, UUID(sid)):
                return SectionOutcome(sid, "complete")  # this run already wrote it (WA-5)
            steps = EngineSectionSteps(self._api, ctx, ref, ctx.input.is_regeneration)
            trace: Trace = []
            try:
                outcome = await run_section_graph(steps, trace)
            finally:
                await self._checkpoint(ctx, sid, trace)
            total, retried = steps.tokens
            activity_metrics().tokens(job.stage, _tier(ctx), total, retried)
            return outcome

        started = time.monotonic()
        result = await self._run(
            "write_section",
            run_input,
            body,
            gone=SectionOutcome(sid, "failed", skipped_terminal=True),
            terminal=SectionOutcome(sid, "failed", skipped_terminal=True),
            quota=quota,
            beat=(sid, job.stage),
        )
        # per attempt, for returned results (a raised failure is counted in activity_failures_total)
        label = (
            "quota_suspended"
            if result.quota_suspended
            else "skipped_terminal"
            if result.skipped_terminal
            else result.state
        )
        activity_metrics().section(job.stage, label, time.monotonic() - started)
        return result

    async def _checkpoint(self, ctx: Ctx, section_id: str, trace: Trace) -> None:
        """Names, timings and counts, for inspection only (WA-10, WA-11). Never fails a section."""
        info = activity.info()
        run_id = f"{info.workflow_run_id}:{info.activity_id}:{info.attempt}"
        try:
            await self._api.record_checkpoint(ctx, UUID(section_id), run_id, list(trace))
        except Exception:  # noqa: BLE001 - an inspection row must never fail a written section
            return

    @activity.defn(name="capture_facts")
    async def capture_facts(self, req: FactsRequest) -> StepResult:
        """Stage 1's document-wide facts, from the foundation sections' text in memory (WP-16)."""

        async def body(ctx: Ctx) -> StepResult:
            await self._api.reresolve(ctx)
            bundle = await self._api.load_pack(ctx)
            keys = self._api.fact_keys(bundle)
            if not keys or not req.section_ids:
                return StepResult(count=0)
            chunks = [
                self._api.chunk_view(await _section_chunk(self._api, ctx, sid))
                for sid in req.section_ids
            ]
            answer = await self._ask(
                ctx,
                "facts",
                chunks,
                "Return JSON "
                + '{"facts": [{"key", "value", "section"}]}'
                + f" for keys: {', '.join(keys)}.",
            )
            drafts = _parse_facts(self._api, answer, set(req.section_ids), set(keys))
            return StepResult(count=await self._api.record_facts(ctx, drafts))

        return await self._run(
            "capture_facts",
            req.run_input,
            body,
            gone=_GONE,
            terminal=_TERMINAL,
            quota=_QUOTA_STEP,
            beat=("facts",),
        )

    @activity.defn(name="coherence_pass")
    async def coherence_pass(self, req: RunRequest) -> CoherenceStep:
        """Compare fact keys across sections; write the verdict. A repeat is not retried."""

        async def body(ctx: Ctx) -> CoherenceStep:
            await self._api.reresolve(ctx)
            bundle = await self._api.load_pack(ctx)
            keys = self._api.fact_keys(bundle)
            state: RunState = await self._api.run_state(ctx)
            done = sorted(
                (s for s in state.section_refs if s.state == "complete"), key=lambda s: s.ordinal
            )
            if not keys or len(done) < 2:
                result = await self._api.record_coherence(ctx, "consistent", [])
                return CoherenceStep(result.verdict, result.conflict_count)
            chunks = [
                self._api.chunk_view(await _section_chunk(self._api, ctx, s.section_id))
                for s in done
            ]
            answer = await self._ask(
                ctx,
                "coherence",
                chunks,
                "Return JSON "
                + '{"values": {"<section>": {"<key>": "<value>"}}}'
                + f" for keys: {', '.join(keys)}.",
            )
            conflicts = _conflicts(self._api, answer, [s.section_id for s in done], set(keys))
            result = await self._api.record_coherence(
                ctx, "conflicted" if conflicts else "consistent", conflicts
            )
            return CoherenceStep(result.verdict, result.conflict_count)

        return await self._run(
            "coherence_pass",
            req.run_input,
            body,
            gone=CoherenceStep(gone=True),
            terminal=CoherenceStep(skipped_terminal=True),
            quota=CoherenceStep(quota_suspended=True),
            beat=("coherence",),
        )

    @activity.defn(name="run_extraction")
    async def run_extraction(self, req: RunRequest) -> ExtractionStep:
        """The engine's whole extraction: the code R-4 runs inside a request."""

        async def body(ctx: Ctx) -> ExtractionStep:
            await self._api.reresolve(ctx)
            result: ExtractionResult = await self._api.run_extraction(ctx)
            return ExtractionStep(result)

        return await self._run(
            "run_extraction",
            req.run_input,
            body,
            gone=ExtractionStep(gone=True),
            terminal=ExtractionStep(skipped_terminal=True),
            quota=ExtractionStep(quota_suspended=True),
            beat=("extract",),
        )

    async def _ask(self, ctx: Ctx, label: str, chunks: list[Any], instruction: str) -> str:
        """One JSON-task model call over in-memory text. The answer never leaves the activity."""
        prompt = self._api.compose_prompt(
            task=_FACT_TASK,
            skeleton="Answer with JSON only.",
            tenant_overrides="",
            user_context="",
            chunks=chunks,
            user_prompt=instruction,
        )
        answer = await self._api.call_model(
            ctx,
            _FACT_TASK,
            prompt,
            section_ref=label,
            attempt=1,
            deadline_seconds=_MODEL_DEADLINE_SECONDS,
        )
        return str(answer.text)


# -- helpers: parsing a model's JSON into engine drafts, in memory ---------------------------------
@dataclass(frozen=True, slots=True)
class _TextChunk:
    """A section's text as one prompt chunk, in memory (the chunk id is the section id)."""

    chunk_id: str
    text: str
    document_title: str = ""
    heading_path: str | None = None
    clause_ref: str | None = None
    document_role: str = ""
    document_id: UUID | None = None
    document_version: int = 0
    language: str = "en"
    excerpt: str = ""


async def _section_chunk(api: EngineApi, ctx: Ctx, section_id: str) -> _TextChunk:
    return _TextChunk(section_id, await api.section_text(ctx, UUID(section_id)))


MAX_FACT_VALUE_CHARS = 2000  # a fact is a value (a date, an amount, a name), not prose


def _loads(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except TypeError, ValueError, RecursionError:  # RecursionError: deeply nested model output
        raise ERRORS.SchemaViolation from None  # content-free by construction
    if not isinstance(data, dict):
        raise ERRORS.SchemaViolation
    return data


def _parse_facts(api: EngineApi, raw: str, sections: set[str], keys: set[str]) -> list[Any]:
    out: list[Any] = []
    facts = _loads(raw).get("facts") or []
    if not isinstance(facts, list):
        raise ERRORS.SchemaViolation
    for item in facts:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or not isinstance(item.get("section"), str)
            or "value" not in item
            or item["key"] not in keys
            or item["section"] not in sections
            or len(str(item["value"])) > MAX_FACT_VALUE_CHARS
        ):
            continue  # one malformed or unknown item is dropped; the rest are kept
        out.append(
            api.fact_draft(
                fact_key=str(item["key"]),
                value=str(item["value"]),
                section_id=UUID(str(item["section"])),
            )
        )
    return out


def _conflicts(api: EngineApi, raw: str, ordered: list[str], keys: set[str]) -> list[Any]:
    values = _loads(raw).get("values") or {}
    if not isinstance(values, dict):
        raise ERRORS.SchemaViolation
    established: dict[str, tuple[str, str]] = {}
    found: list[Any] = []
    for sid in ordered:  # the earliest section that states a key establishes it
        stated = values.get(sid)
        if not isinstance(stated, dict):
            continue
        for key in keys & set(stated):
            raw = stated[key]
            if not isinstance(raw, str | int | float) or len(str(raw)) > MAX_FACT_VALUE_CHARS:
                continue  # the same bounds as a captured fact: a value, not prose or a structure
            value = str(raw).strip()
            if key not in established:
                established[key] = (value, sid)
            elif value.casefold() != established[key][0].casefold():
                found.append(
                    api.conflict_draft(
                        section_id=UUID(sid),
                        fact_key=key,
                        established_value=established[key][0],
                        other_value=value,
                        established_by_section_id=UUID(established[key][1]),
                    )
                )
    return found


def _entry(template: Any, ref: str, section_type: str, is_foundation: bool) -> Any:
    """An ``OutlineEntry`` shaped like the pack's own."""
    return type(template)(ref=ref, section_type=section_type, is_foundation=is_foundation)


def _tier(ctx: Ctx) -> str:
    return "strict" if ctx.input.residency_mode != "standard" else "standard"
