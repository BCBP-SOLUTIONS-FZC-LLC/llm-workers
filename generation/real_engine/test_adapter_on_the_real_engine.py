"""The workers' adapter and activities against the REAL ``rag.engine`` 1.1.0 on real Postgres.

Everything below goes through ``RagEngineApi`` (``activities/engine_api.py``) and the workers'
activities, never through the engine's functions directly. The engine logs in as a non-superuser
member of ``rag_activity`` (a superuser would bypass RLS even with FORCE), on a migrated
``llm_generation`` and ``llm_corpus``; sections are stored in an S3 emulator; the pack comes from a
Tender stub over HTTP. What is real and what is a stand-in is stated per test.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import aioboto3
import asyncpg
import infra
import pytest
from conftest import BUCKET, CAPABILITY, REGION, Stack
from llm_workers_generation.activities.engine_api import RagEngineApi
from llm_workers_generation.activities.generation import GenerationActivities
from llm_workers_generation.types import FactsRequest
from rag.application.verification.verify import ClaimResult, VerificationResult
from rag.domain.shared.enums import ClaimOutcome, CompletenessVerdict, EvidenceTier
from rag.domain.verification.completeness import Completeness
from rag.domain.verification.scoring import Disposition
from rag.domain.workflows.input import ScopeSeed, WorkflowInput
from rag.engine import ContentUnavailable, RunGone
from rag.infrastructure.storage.artefact_content_store import S3ArtefactContentStore
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PRINCIPAL = UUID("0a3f0000-0000-7000-8000-000000000002")
TEXT = b"the real section text: CANARY-7d1c"


# -- seeding: what the API's admission writes, as a superuser (the engine never inserts these) ---------
async def seed_run(
    stack: Stack, tenant: UUID, *, status: str = "pending", gated: bool = False
) -> tuple[dict[str, Any], UUID, UUID]:
    run_id, art, tender = uuid4(), uuid4(), uuid4()
    winput = WorkflowInput(
        tenant_id=tenant,
        principal_id=PRINCIPAL,
        artefact_id=art,
        generation_run_id=run_id,
        capability=CAPABILITY,
        requested_mode="assistance",
        plan="standard",
        residency_mode="standard",
        domain_profile_version="dp-1",
        language_pack_version="lp-1",
        scope_seed=ScopeSeed(tender),
        tender_id=tender,
        correlation_id="corr-1",
        outline_gate=gated,
    ).to_dict()
    conn = await stack.admin()
    try:
        await conn.execute(
            "INSERT INTO artefacts (id, tenant_id, tender_id, kind, capability, created_by, status) "
            "VALUES ($1, $2, $3, 'document', $4, $5, 'pending'::artefact_status)",
            art, tenant, tender, CAPABILITY, PRINCIPAL,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO generation_runs (id, artefact_id, tenant_id, tender_id, principal_id, "
            "origin, workflow_id, run_input, requested_mode, effective_mode, eligibility_state, "
            "domain_profile_version, language_pack_version, status, suspended_at) "
            "VALUES ($1, $2, $3, $4, $5, 'original'::artefact_origin, $6, $7::jsonb, 'assistance', "
            "'assistance', 'GENERATABLE', 'dp-1', 'lp-1', $8::run_status, "
            "CASE WHEN $8 = 'suspended' THEN now() END)",
            run_id, art, tenant, tender, PRINCIPAL, f"wf-{art}", json.dumps(winput), status,
        )  # fmt: skip
    finally:
        await conn.close()
    return winput, run_id, art


async def retrieval_set(stack: Stack, tenant: UUID, art: UUID) -> UUID:
    conn = await stack.admin()
    try:
        return await conn.fetchval(  # type: ignore[no-any-return]
            "INSERT INTO retrieval_sets (tenant_id, tender_id, artefact_id, verdict, "
            "scope_resolution_hash, grant_level_applied, filter_set, index_generations, "
            "profile_source, applied_profile, reranker_ran, budget_allocation, "
            "domain_profile_version, language_pack_version) "
            "VALUES ($1, $2, $3, 'sufficient', 'sha256:1c', 'answer', '{}'::jsonb, '{}'::jsonb, "
            "'section_type', '{}'::jsonb, true, '{}'::jsonb, 'dp-1', 'lp-1') RETURNING id",
            tenant, uuid4(), art,
        )  # fmt: skip
    finally:
        await conn.close()


async def row(stack: Stack, sql: str, *args: Any) -> asyncpg.Record | None:
    conn = await stack.admin()
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


async def events(stack: Stack, art: UUID) -> list[str]:
    conn = await stack.admin()
    try:
        rows = await conn.fetch(
            "SELECT event_type FROM outbox_events WHERE payload::text LIKE $1 ORDER BY created_at",
            f"%{art}%",
        )
        return [r["event_type"] for r in rows]
    finally:
        await conn.close()


def draft(api: RagEngineApi, rset: UUID) -> Any:
    verification = VerificationResult(
        claims=(ClaimResult(1, ClaimOutcome.SUPPORTED, ("v1:1",), (), 1),),
        supported=1, unsupported=0, miscited=0, gaps=0, tiers_run=(1,), score=Decimal("1.000"),
        tier=EvidenceTier.SUPPORTED,
        completeness=Completeness(CompletenessVerdict.COMPLETE, 1, 1, ()),
        disposition=Disposition.SURFACE_FLAGGED, judge_model_profile_id="judge-small",
    )  # fmt: skip
    return api.section_draft(
        content=TEXT,
        retrieval_set_id=rset,
        prompt_hash="ph-1",
        model_profile_id="mp-1",
        serving_tier="primary",
        verification=verification,
    )


async def written_section(stack: Stack, tenant: UUID) -> tuple[dict[str, Any], UUID, UUID, UUID]:
    """A run with an outline and one saved section (outline from the pack, text in the store)."""
    api = stack.api
    winput, run_id, art = await seed_run(stack, tenant)
    async with api.scope(winput) as ctx:
        bundle = await api.load_pack(ctx)
        entries = api.outline_entries(bundle, CAPABILITY)
        await api.begin_stage(ctx, "outline")
        saved = await api.save_outline(ctx, entries, gated=False)
        sid = UUID(saved.section_ids[0])
        assert await api.begin_section(ctx, sid) is True
        won = await api.save_section(ctx, sid, draft(api, await retrieval_set(stack, tenant, art)))
        assert won.state == "complete" and won.version_id is not None
    return winput, run_id, art, sid


# -- 1. begin_stage and run_state ---------------------------------------------------------------------
async def test_1_begin_stage_and_run_state(stack: Stack) -> None:
    """Real Postgres, real RLS role. ``begin_stage`` writes the row; ``run_state`` reads it back."""
    tenant = uuid4()
    winput, run_id, art = await seed_run(stack, tenant, gated=True)
    async with stack.api.scope(winput) as ctx:
        assert await stack.api.begin_stage(ctx, "outline") is True
        state = await stack.api.run_state(ctx)
    assert (state.status, state.stage, state.outline_state) == (
        "outline_building",
        "outline",
        "building",
    )
    db = await row(stack, "SELECT status, current_stage FROM generation_runs WHERE id = $1", run_id)
    assert db is not None and tuple(db) == ("outline_building", "outline")
    art_row = await row(stack, "SELECT status FROM artefacts WHERE id = $1", art)
    assert art_row is not None and art_row["status"] == "generating"  # the artefact left `pending`


# -- 2. fail_run from the three states ----------------------------------------------------------------
@pytest.mark.parametrize("status", ["pending", "outline_building", "suspended"])
async def test_2_fail_run_from_each_state_is_recorded(stack: Stack, status: str) -> None:
    """Gap 5 through the adapter: the row and the artefact fail, one ``GenerationFailed``, stamp cleared."""
    tenant = uuid4()
    winput, run_id, art = await seed_run(stack, tenant, status=status, gated=True)
    async with stack.api.scope(winput) as ctx:
        assert await stack.api.fail_run(ctx, "internal", None) is True
        assert await stack.api.fail_run(ctx, "internal", None) is False  # one terminal event
    db = await row(
        stack,
        "SELECT status, failure_reason, suspended_at FROM generation_runs WHERE id = $1",
        run_id,
    )
    assert db is not None
    assert (db["status"], db["failure_reason"], db["suspended_at"]) == ("failed", "internal", None)
    art_row = await row(stack, "SELECT status FROM artefacts WHERE id = $1", art)
    assert art_row is not None and art_row["status"] == "failed"
    assert (await events(stack, art)).count("GenerationFailed") == 1


# -- 3. save_outline, save_section, load_section_text: the text round-trips through S3 -----------------
async def test_3_the_text_round_trips_through_a_real_s3_store(stack: Stack) -> None:
    """Real Postgres and a real S3 API (floci): the object exists and the text comes back verbatim."""
    tenant = uuid4()
    winput, _, art, sid = await written_section(stack, tenant)
    async with stack.api.scope(winput) as ctx:
        assert await stack.api.section_text(ctx, sid) == TEXT.decode()
        other = (await stack.api.run_state(ctx)).section_refs[1].section_id
        assert await stack.api.section_text(ctx, UUID(other)) == ""  # no version yet
    prefix = f"tenants/{tenant}/artefacts/{art}/sections/{sid}/"
    async with aioboto3.Session().client(
        "s3", endpoint_url=stack.s3_endpoint, region_name=REGION
    ) as s3:
        listed = await s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    (obj,) = listed["Contents"]
    assert obj["Size"] == len(TEXT)


# -- 4. RunGone, ContentUnavailable, and the activity's fixed Internal error --------------------------
async def test_4_another_artefacts_section_is_run_gone(stack: Stack) -> None:
    tenant = uuid4()
    winput, _, _, _ = await written_section(stack, tenant)
    _, _, _, other_sid = await written_section(stack, tenant)  # same tenant, another artefact
    async with stack.api.scope(winput) as ctx:
        with pytest.raises(RunGone):
            await stack.api.section_text(ctx, other_sid)


async def test_4_an_unreachable_store_is_content_unavailable_and_the_activity_ends_internal(
    stack: Stack,
) -> None:
    """The store is the engine's own S3 class pointed at a closed port (the Postgres is real)."""
    tenant = uuid4()
    winput, _, _, sid = await written_section(stack, tenant)
    dead = S3ArtefactContentStore(BUCKET, region=REGION, endpoint_url="http://127.0.0.1:1")
    api = RagEngineApi(dataclasses.replace(stack.api.services, store=dead))
    async with api.scope(winput) as ctx:
        with pytest.raises(ContentUnavailable):
            await api.section_text(ctx, sid)
    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(
            GenerationActivities(api).capture_facts, FactsRequest(winput, (str(sid),))
        )
    err = info.value
    assert err.type == "Internal" and err.message == "internal error"
    assert not err.non_retryable  # retryable and bounded (ADR-0003)
    assert err.__cause__ is None and not err.details
    assert "CANARY" not in str(err) and "127.0.0.1" not in str(err)  # no content, no endpoint


async def test_4_bytes_that_no_longer_hash_to_the_digest_are_content_unavailable(
    stack: Stack,
) -> None:
    """ASV-4 against a real object: overwrite the stored bytes behind the engine's back."""
    tenant = uuid4()
    winput, _, art, sid = await written_section(stack, tenant)
    key = f"tenants/{tenant}/artefacts/{art}/sections/{sid}/v1.md"
    async with aioboto3.Session().client(
        "s3", endpoint_url=stack.s3_endpoint, region_name=REGION
    ) as s3:
        await s3.put_object(Bucket=BUCKET, Key=key, Body=b"tampered")
    async with stack.api.scope(winput) as ctx:
        with pytest.raises(ContentUnavailable):
            await stack.api.section_text(ctx, sid)


# -- 5. the pack through the adapter (Tender is a stub) -----------------------------------------------
async def test_5_the_pack_keys_reach_the_adapter_from_a_tender_stub(stack: Stack) -> None:
    """Tender is a stub over HTTP; the engine's resolver, cache and pack codec are real."""
    tenant = uuid4()
    winput, _, _ = await seed_run(stack, tenant)
    async with stack.api.scope(winput) as ctx:
        bundle = await stack.api.load_pack(ctx)
    entries = stack.api.outline_entries(bundle, CAPABILITY)
    assert [(e.ref, e.section_type, e.is_foundation) for e in entries] == [
        ("1", "executive_summary", True),
        ("2", "methodology", False),
        ("3", "pricing", False),
    ]
    assert stack.api.fact_keys(bundle) == ("contract_value", "client_name")
    assert any(r.startswith("/internal/packs/resolve") for r in stack.tender.requests)


async def test_5_a_malformed_pack_is_downgraded_by_the_engine_and_fails_the_outline(
    stack: Stack,
) -> None:
    """The engine reads a bad ``outline_skeletons`` shape as ``PackUnavailable`` and falls back to the
    platform default profile (no outline). The adapter then reports ``PackOutlineMissing``."""
    from llm_workers_generation.activities.engine_api import PackOutlineMissing

    bad = infra.pack_document(CAPABILITY)
    bad["domain_profile"]["outline_skeletons"] = {CAPABILITY: "not a list"}
    stack.tender.serve(bad)
    try:
        winput, _, _ = await seed_run(stack, uuid4())  # a new tenant: the pack cache is per tenant
        async with stack.api.scope(winput) as ctx:
            bundle = await stack.api.load_pack(ctx)
        with pytest.raises(PackOutlineMissing):
            stack.api.outline_entries(bundle, CAPABILITY)
    finally:
        stack.tender.serve(infra.pack_document(CAPABILITY))


# -- 6. tenant isolation -------------------------------------------------------------------------------
async def test_6_one_tenants_activity_cannot_read_anothers_section(stack: Stack) -> None:
    """RLS as the non-superuser login: tenant A gets ``RunGone`` for tenant B's section, never content."""
    a, b = uuid4(), uuid4()
    winput_a, _, _ = await seed_run(stack, a)
    _, _, _, sid_b = await written_section(stack, b)
    async with stack.api.scope(winput_a) as ctx:
        with pytest.raises(RunGone):
            await stack.api.section_text(ctx, sid_b)
    # the proof is not vacuous: the login is not a superuser, so the policy decides what it sees
    conn = await asyncpg.connect(stack.dsn("llm_generation", "rag_activity_login", "pw"))
    try:
        assert not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        assert await conn.fetchval("SELECT count(*) FROM artefact_sections") == 0  # no tenant bound
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(a))
        assert (
            await conn.fetchval("SELECT count(*) FROM artefact_sections WHERE id = $1", sid_b) == 0
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(b))
        assert (
            await conn.fetchval("SELECT count(*) FROM artefact_sections WHERE id = $1", sid_b) == 1
        )
    finally:
        await conn.close()


async def test_7_a_tender_outage_is_a_silent_default_pack_and_the_outline_fails_for_good(
    stack: Stack,
) -> None:
    """ENGINE FINDING (not worked around): ``load_pack`` hides a Tender outage.

    Call: ``rag.engine.load_pack(ctx)`` for a run whose pack Tender cannot serve (HTTP 503 after the
    resolver's retries). Result: no error; ``resolve_or_default`` returns the platform default profile
    (``PackSource.DEFAULT``, empty ``outline_skeletons``). The adapter cannot tell that from a pack
    that has no outline, so ``outline`` raises ``PackOutlineMissing`` (non-retryable) and the run
    fails ``internal``, where a retry once Tender is back would have worked. Last in the file: the
    resolver's circuit breaker stays open afterwards. Recorded in ADR-0003 for the engine owner.
    """
    from llm_workers_generation.activities.engine_api import PackOutlineMissing

    stack.tender.status = 503
    try:
        winput, _, _ = await seed_run(stack, uuid4())
        async with stack.api.scope(winput) as ctx:
            bundle = await stack.api.load_pack(ctx)  # no exception: the outage is swallowed
        assert str(bundle.source).lower().endswith("default")
        assert bundle.domain_profile.outline_skeletons == {}
        with pytest.raises(PackOutlineMissing):
            stack.api.outline_entries(bundle, CAPABILITY)
    finally:
        stack.tender.status = 200
