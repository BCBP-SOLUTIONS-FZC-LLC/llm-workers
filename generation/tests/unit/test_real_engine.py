"""The adapter against the *real* engine wheel (a subprocess: the suites fake ``rag.engine`` in-process).

Skipped where the wheel is not installed. Three jobs: (1) every function the adapter delegates to exists
with the parameters the adapter passes, so a drifted signature fails here and not in production;
(2) pin what engine 1.1.0 closed (ADR-0003 gaps 1 to 5): the pack fields, ``load_section_text``, the
exported types and the ``failed`` edges of the run state machine, all read from the real engine;
(3) pin gap 6 (the metrics identity, engine 1.2.0): the engine's metrics are refused under the
workers' identity until 1.2.0 is adopted. Pure and in-process: **no database, no object store**.
"""

import json
import subprocess
import sys

import pytest

SCRIPT = r"""
import dataclasses, inspect, json
import rag.engine as e
from llm_workers_generation.activities import engine_api
from llm_workers_generation.activities.engine_api import RagEngineApi

DELEGATED = ["reresolve", "run_state", "begin_stage", "load_pack", "save_outline", "begin_section",
    "save_section", "save_section_gap", "fail_section", "retrieve", "call_model", "verify",
    "record_facts", "record_coherence", "record_checkpoint", "mint_document_version", "run_extraction",
    "suspend_for_quota", "complete_run", "fail_run", "translate"]

out = {"mismatch": {}, "missing": [], "gaps": {}}
out["load_section_text_params"] = list(inspect.signature(e.load_section_text).parameters)
for name in DELEGATED:
    real = getattr(e, name, None)
    if real is None:
        out["missing"].append(name); continue
    mine = set(inspect.signature(getattr(RagEngineApi, name)).parameters) - {"self", "kwargs"}
    params = inspect.signature(real).parameters
    theirs, required = set(params), {n for n, p in params.items() if p.default is inspect.Parameter.empty}
    if not required <= mine <= theirs:  # the adapter may omit a parameter that has a default
        out["mismatch"][name] = [sorted(mine), sorted(theirs)]
for name in ["OutlineEntry", "SectionDraft", "GapDraft", "FactDraft", "ConflictDraft", "run_scope",
             "compose_prompt", "refine_instruction", "ScopeRevoked", "QuotaRefused", "RunGone", "RunTerminal",
             "ResidencyRefused", "SchemaViolation", "InferenceUnavailable", "NothingToMint"]:
    if not hasattr(e, name): out["missing"].append(name)
fields = {f.name for f in dataclasses.fields(__import__("rag.domain.packs.profile", fromlist=["DomainProfile"]).DomainProfile)}
from types import SimpleNamespace
from rag.domain.generation.run import RUN_TRANSITIONS
from rag.domain.packs.profile import SkeletonSection
from rag.domain.shared.enums import RunStatus
out["gaps"] = {
    "outline_skeletons_in_pack": "outline_skeletons" in fields,
    "fact_keys_in_pack": "fact_keys" in fields,
    "load_section_text": hasattr(e, "load_section_text"),
}
out["engine_version"] = e.ENGINE_API_VERSION
out["exported_types"] = {n: hasattr(e, n) for n in ["PromptChunk", "EvidenceChunk",
    "VerificationRequest", "VerificationResult", "ComposedPrompt", "ContentUnavailable"]}
out["skeleton_is_namedtuple"] = issubclass(SkeletonSection, tuple) and hasattr(SkeletonSection, "_fields")
out["failed_reachable_from"] = sorted(s.value for s, to in RUN_TRANSITIONS.items() if RunStatus.FAILED in to)
# the adapter builds the REAL types (no look-alikes) from a pack and a candidate
api = RagEngineApi(None)
bundle = SimpleNamespace(domain_profile=SimpleNamespace(
    outline_skeletons={"cap": (SkeletonSection("1", "intro", True),)}, fact_keys=("k",)))
[entry] = api.outline_entries(bundle, "cap")
cand = SimpleNamespace(chunk_id="c", text="t", document_title="d", heading_path=None, clause_ref=None,
    document_role="r", document_id=None, document_version=1, language="en", excerpt="")
out["adapter_builds_real_types"] = [
    type(entry) is e.OutlineEntry and (entry.ref, entry.section_type, entry.is_foundation) == ("1", "intro", True),
    type(api.chunk_view(cand)) is e.PromptChunk,
    type(api.evidence_view(cand)) is e.EvidenceChunk,
    type(api.verify_request(text="t", evidence=(), refs={})) is e.VerificationRequest,
]
# the engine's own Tier 3 metrics cannot be built under this service's metrics identity (ADR-0006)
from prometheus_client import CollectorRegistry
from fastapicommon.metrics import MetricsOptions, init_metrics
init_metrics(MetricsOptions("llm-workers", "t", "llm", "dev"), registry=CollectorRegistry())
try:
    from rag.infrastructure.observability.metrics import PrometheusObserver
    PrometheusObserver()
    out["engine_metrics_under_worker_identity"] = "built"
except ValueError:
    out["engine_metrics_under_worker_identity"] = "refused"
from rag.infrastructure.config import Settings as RagSettings
out["engine_metrics_switch"] = RagSettings(enable_metrics=False).enable_metrics
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def report() -> dict:  # type: ignore[type-arg]
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, cwd="/", check=False
    )
    if "No module named 'rag" in proc.stderr:
        pytest.skip("the engine wheel is not installed")
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_delegated_function_exists_with_the_parameters_the_adapter_passes(report) -> None:  # noqa: ANN001
    assert report["missing"] == []
    assert report["mismatch"] == {}


def test_engine_1_1_0_closes_adr_0003_gaps_1_to_5(report) -> None:  # noqa: ANN001
    """The pack fields, ``load_section_text`` and the exported types exist in the installed engine."""
    assert report["engine_version"] == "1.1.0"
    assert report["gaps"] == {
        "outline_skeletons_in_pack": True,
        "fact_keys_in_pack": True,
        "load_section_text": True,
    }
    assert report["load_section_text_params"] == ["ctx", "section_id"]
    assert all(report["exported_types"].values()), report["exported_types"]
    assert report["skeleton_is_namedtuple"] is True


def test_the_adapter_builds_the_engines_own_types_not_look_alikes(report) -> None:  # noqa: ANN001
    assert report["adapter_builds_real_types"] == [True, True, True, True]


def test_fail_run_has_an_edge_from_every_non_terminal_state(report) -> None:  # noqa: ANN001
    """Gap 5: ``failed`` is reachable from ``pending``, ``outline_building`` and ``suspended`` too."""
    assert report["failed_reachable_from"] == [
        "awaiting_outline_approval",
        "generating",
        "outline_building",
        "pending",
        "suspended",
    ]


def test_the_engines_metrics_cannot_share_a_process_with_the_workers_identity(report) -> None:  # noqa: ANN001
    """ADR-0003 gap 6 and ADR-0006: fastapicommon allows one identity per process, so ``build_rag_engine``
    turns the engine's metrics off. Engine 1.1.0 does not change this (1.2.0 does, not adopted). When this
    starts failing, the engine took the caller's identity: revisit that switch and re-pin."""
    assert report["engine_metrics_under_worker_identity"] == "refused"
    assert report["engine_metrics_switch"] is False
