"""WB-4: the engine's public API against its recorded snapshot, and the fake against both.

The snapshot (``tests/contract/engine_api_v1.json``) is the copy of the engine's own
``engine_api_v1.json`` at the pinned tag. A scheduled job opens a pull request when a new engine
tag appears (LLD §4.6); this test, run against the installed engine, is what blocks a breaking
change from reaching a worker unnoticed. The ``fake`` tests run everywhere and keep the fake
honest: it must offer every function and error the port uses.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SNAPSHOT = json.loads((Path(__file__).parents[1] / "contract" / "engine_api_v1.json").read_text())
ALLOWED_FUNCTIONS = {
    "build_engine",
    "run_scope",
    "reresolve",
    "run_state",
    "begin_stage",
    "load_pack",
    "save_outline",
    "begin_section",
    "save_section",
    "save_section_gap",
    "fail_section",
    "load_section_text",
    "retrieve",
    "compose_prompt",
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
    "translate",
    "gateway_context",
}


def test_snapshot_is_the_allowed_list_the_service_was_specified_against() -> None:
    assert set(SNAPSHOT["functions"]) == ALLOWED_FUNCTIONS
    assert SNAPSHOT["engine_api_version"].startswith("1.")  # WB-2: pinned to a major version


def test_the_real_engine_matches_the_snapshot_when_installed() -> None:
    # a subprocess: the suites replace ``rag.engine`` with the fake in this process (conftest)
    proc = subprocess.run(
        [sys.executable, "-m", "rag.engine.snapshot"],
        capture_output=True,
        text=True,
        cwd="/",
        check=False,
    )
    if "No module named" in proc.stderr:
        pytest.skip("the engine wheel is not installed")
    assert proc.returncode == 0, proc.stderr[-500:]
    assert json.loads(proc.stdout) == SNAPSHOT  # fails if the shape moved without a version change


def test_the_fake_engine_offers_every_error_and_the_port_functions() -> None:
    import fake_engine

    for name in SNAPSHOT["errors"]:
        assert hasattr(fake_engine, name), f"the fake lacks {name}"


def test_the_adapter_and_the_fake_implement_the_same_port() -> None:
    from fake_engine import FakeEngineApi
    from llm_workers_generation.activities.engine_api import RagEngineApi
    from llm_workers_generation.activities.ports import EngineApi

    port = {n for n in vars(EngineApi) if not n.startswith("_")} | {"services"}
    for impl in (RagEngineApi, FakeEngineApi):
        missing = {n for n in port if n != "services" and not hasattr(impl, n)}
        assert not missing, f"{impl.__name__} lacks {sorted(missing)}"
