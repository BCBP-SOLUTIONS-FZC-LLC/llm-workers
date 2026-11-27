"""Test bootstrap: the plain types and a fake ``rag.engine`` where the real engine is not installed.

The real engine needs Python 3.15 and the API's whole dependency tree, so the unit and workflow
suites run against ``fakes/fake_engine.py`` (the "fake Engine" of LLD §18.1). CI installs the real
wheel and then runs the contract tests (``test_engine_contract.py``) that compare the two.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fakes"))

SIBLING = Path(os.environ.get("RAG_SRC", HERE.parents[2] / "llm-retrieval-generation" / "src"))


def _load_plain_types() -> None:  # the real llm_models is used where installed
    try:
        importlib.import_module("llm_models.workers")
        return
    except ImportError:
        pass
    pkg = SIBLING / "llm_models"
    spec = importlib.util.spec_from_file_location(
        "llm_models", pkg / "__init__.py", submodule_search_locations=[str(pkg)]
    )
    assert spec and spec.loader, f"llm_models not found at {pkg}; set RAG_SRC"
    module = importlib.util.module_from_spec(spec)
    sys.modules["llm_models"] = module
    spec.loader.exec_module(module)
    importlib.import_module("llm_models.workers")


def _install_fake_engine() -> None:
    """Always run the suites against the fake engine, even where the real wheel is installed.

    The real ``rag`` package stays importable (``rag.domain`` for the ``ENTERPRISE_PLANS`` check) and
    the contract test runs the real engine's own snapshot in a subprocess, so nothing here hides it.
    """
    import types

    import fake_engine

    if "rag" not in sys.modules:
        try:
            importlib.import_module("rag")
        except ImportError:
            sys.modules["rag"] = types.ModuleType("rag")
    sys.modules["rag.engine"] = fake_engine


_load_plain_types()
_install_fake_engine()


import pytest  # noqa: E402


@pytest.fixture
def app_registry():  # noqa: ANN201
    """A private collector registry with fastapicommon metrics initialised for the workers.

    ``init_metrics(registry=...)`` replaces the process's active collectors, so each test gets its own.
    """
    from fastapicommon.metrics import MetricsOptions, init_metrics
    from prometheus_client import CollectorRegistry

    registry = CollectorRegistry()
    init_metrics(MetricsOptions("llm-workers", "test", "llm", "test"), registry=registry)
    yield registry


@pytest.fixture(autouse=True)
def _restore_root_logger():  # noqa: ANN202
    """``init_observability`` (called by ``worker.run``) replaces the root logger's handlers and level.

    Left in place it would put fastapicommon's formatter, at DEBUG, on every later test: a failing
    activity then logs a formatted stack each time, which slows the retry-heavy workflow tests a lot.
    """
    import logging

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
