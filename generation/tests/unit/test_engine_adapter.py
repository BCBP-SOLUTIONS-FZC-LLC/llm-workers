"""``RagEngineApi`` delegates to ``rag.engine`` 1.1.0 (the pack fields, ``load_section_text``, the exported types)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from llm_workers_generation.activities import engine_api
from llm_workers_generation.activities.engine_api import (
    EngineApiGap,
    PackOutlineMissing,
    RagEngineApi,
)
from rag.domain.packs.profile import SkeletonSection


def fake_engine_module(**funcs):  # noqa: ANN003, ANN201
    async def passthrough(*a, **k):  # noqa: ANN002, ANN003, ANN202
        return ("called", a, k)

    names = [
        "reresolve",
        "run_state",
        "begin_stage",
        "load_pack",
        "save_outline",
        "begin_section",
        "save_section",
        "save_section_gap",
        "fail_section",
        "retrieve",
        "call_model",
        "verify",
        "refine_instruction",
        "record_facts",
        "record_coherence",
        "record_checkpoint",
        "mint_document_version",
        "run_extraction",
        "suspend_for_quota",
        "complete_run",
        "fail_run",
    ]
    ns = {n: passthrough for n in names}
    ns.update(
        compose_prompt=lambda **k: ("prompt", k),
        translate=lambda e: e,
        run_scope=lambda services, raw: ("scope", services, raw),
        OutlineEntry=lambda **k: SimpleNamespace(**k),
        SectionDraft=lambda **k: SimpleNamespace(**k),
        GapDraft=lambda **k: SimpleNamespace(**k),
        FactDraft=lambda **k: SimpleNamespace(**k),
        ConflictDraft=lambda **k: SimpleNamespace(**k),
        PromptChunk=lambda **k: SimpleNamespace(**k),
        EvidenceChunk=lambda **k: SimpleNamespace(**k),
        VerificationRequest=lambda **k: SimpleNamespace(**k),
    )
    ns.update(funcs)
    return SimpleNamespace(**ns)


@pytest.fixture
def api(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(engine_api, "engine", fake_engine_module())
    return RagEngineApi(services="svc")


async def test_every_call_delegates_to_the_engine(api) -> None:  # noqa: ANN001
    sid = uuid4()
    assert api.scope({"a": 1}) == ("scope", "svc", {"a": 1})
    for call in (
        api.reresolve("c"),
        api.run_state("c"),
        api.begin_stage("c", "stage1"),
        api.load_pack("c"),
        api.save_outline("c", [], gated=True),
        api.begin_section("c", sid),
        api.save_section("c", sid, "d"),
        api.save_section_gap("c", sid, "g"),
        api.fail_section("c", sid, "r"),
        api.retrieve("c", "q", section_type="t", limit=3),
        api.call_model("c", "draft", "p", section_ref="1", attempt=1, deadline_seconds=1.0),
        api.verify("c", "r", prompt="p", deadline_seconds=1.0, section_ref="1", attempt=1),
        api.refine_instruction("c", x=1),
        api.record_facts("c", []),
        api.record_coherence("c", "v", []),
        api.mint_document_version("c"),
        api.run_extraction("c"),
        api.suspend_for_quota("c"),
        api.complete_run("c", "m"),
        api.fail_run("c", "internal", "stage1"),
    ):
        assert (await call)[0] == "called"
    assert await api.record_checkpoint("c", sid, "run", []) is None
    assert api.compose_prompt(task="draft")[0] == "prompt"
    exc = ValueError()
    assert api.translate(exc) is exc


def test_drafts_and_views(api) -> None:  # noqa: ANN001
    assert api.section_draft(content=b"x").content == b"x"
    assert api.gap_draft(reason="r").reason == "r"
    assert api.fact_draft(fact_key="k").fact_key == "k"
    assert api.conflict_draft(fact_key="k").fact_key == "k"
    chunk = SimpleNamespace(
        chunk_id="c",
        text="t",
        document_title="d",
        heading_path=None,
        clause_ref=None,
        document_role="r",
        document_id=None,
        document_version=1,
        language="en",
        excerpt="",
    )
    assert api.chunk_view(chunk).text == "t" and api.evidence_view(chunk).chunk_id == "c"
    assert api.verify_request(text="t", evidence=(), refs={}).text == "t"


def test_pack_helpers_read_the_real_profile_fields(api) -> None:  # noqa: ANN001
    skeleton = {"cap": (SkeletonSection("1", "intro", True), SkeletonSection("2", "scope"))}
    profile = SimpleNamespace(outline_skeletons=skeleton, fact_keys=("k",))
    bundle = SimpleNamespace(domain_profile=profile)
    entries = api.outline_entries(bundle, "cap")
    assert [(e.ref, e.section_type, e.is_foundation) for e in entries] == [
        ("1", "intro", True),
        ("2", "scope", False),
    ]
    assert api.fact_keys(bundle) == ("k",)
    empty = SimpleNamespace(domain_profile=SimpleNamespace(outline_skeletons={}, fact_keys=()))
    assert api.fact_keys(empty) == ()  # a pack with no fact keys is not an error


def test_a_pack_with_no_outline_for_the_capability_is_a_pack_error_not_an_engine_gap(api) -> None:  # noqa: ANN001
    profile = SimpleNamespace(outline_skeletons={"cap": (SkeletonSection("1", "intro"),)})
    bundle = SimpleNamespace(domain_profile=profile)
    with pytest.raises(PackOutlineMissing):
        api.outline_entries(bundle, "other-capability")
    with pytest.raises(PackOutlineMissing):  # the engine reads an absent key as empty
        api.outline_entries(
            SimpleNamespace(domain_profile=SimpleNamespace(outline_skeletons={})), "cap"
        )


async def test_section_text_reads_through_load_section_text(monkeypatch, api) -> None:  # noqa: ANN001
    seen = []

    async def load(ctx, sid):  # noqa: ANN001, ANN202
        seen.append((ctx, sid))
        return "text"

    sid = uuid4()
    monkeypatch.setattr(engine_api, "engine", fake_engine_module(load_section_text=load))
    assert await api.section_text("c", sid) == "text"
    assert seen == [("c", sid)]


async def test_an_engine_gap_maps_to_a_non_retryable_internal_error() -> None:
    from fake_engine import FakeDb, FakeEngineApi
    from llm_workers_generation.activities.generation import GenerationActivities
    from llm_workers_generation.types import FactsRequest
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    db = FakeDb(status="generating")
    db.add_sections([("1", "intro", True)])
    fake = FakeEngineApi(db)

    async def gap(ctx, sid):  # noqa: ANN001, ANN202
        raise EngineApiGap

    fake.section_text = gap  # type: ignore[method-assign]
    from fake_engine import make_input

    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(
            GenerationActivities(fake).capture_facts,
            FactsRequest(make_input(), (db.sections[0]["id"],)),
        )
    assert info.value.non_retryable and info.value.message == "internal error"


async def test_a_pack_without_an_outline_maps_to_a_non_retryable_internal_error() -> None:
    from fake_engine import FakeDb, FakeEngineApi
    from llm_workers_generation.activities.generation import GenerationActivities
    from llm_workers_generation.types import OutlineRequest
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    fake = FakeEngineApi(FakeDb())

    def missing(bundle, capability):  # noqa: ANN001, ANN202
        raise PackOutlineMissing

    fake.outline_entries = missing  # type: ignore[method-assign]
    from fake_engine import make_input

    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(
            GenerationActivities(fake).outline, OutlineRequest(make_input(), None)
        )
    assert info.value.non_retryable and info.value.message == "internal error"


async def test_content_unavailable_is_retryable_and_content_free() -> None:
    """``load_section_text`` turns any read failure into ``ContentUnavailable`` (ADR-0003).

    A transient store or database blip must not fail the section, so it is retryable: the
    activity's bounded attempts decide. The error that leaves the activity is the fixed ``Internal``.
    """
    from fake_engine import ContentUnavailable, FakeDb, FakeEngineApi, make_input
    from llm_workers_generation.activities.generation import GenerationActivities
    from llm_workers_generation.types import FactsRequest
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    db = FakeDb(status="generating")
    db.add_sections([("1", "intro", True)])
    fake = FakeEngineApi(db)

    async def blip(ctx, sid):  # noqa: ANN001, ANN202
        raise ContentUnavailable

    fake.section_text = blip  # type: ignore[method-assign]
    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(
            GenerationActivities(fake).capture_facts,
            FactsRequest(make_input(), (db.sections[0]["id"],)),
        )
    assert not info.value.non_retryable
    assert info.value.type == "Internal" and info.value.message == "internal error"
    assert not info.value.__cause__ and not info.value.details  # nothing of the cause travels
