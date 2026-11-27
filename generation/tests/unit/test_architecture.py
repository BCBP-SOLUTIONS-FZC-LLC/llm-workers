"""Architecture rules WD-1..WD-7 and WB-1 (LLD §3.3, §4.5, §18.4). Fails the build on a breach."""

import ast
import re
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "llm_workers_generation"

SANDBOX_SAFE_OWN = (
    "llm_workers_generation.types",
    "llm_workers_generation.tuning",
    "llm_workers_generation.errors",
    "llm_workers_generation.queues",
    "llm_workers_generation.observability.metrics",
    "llm_workers_generation.workflows",
)
# Rule 2: no worker code touches a database. Pools exist only through rag.engine.bootstrap.build_engine.
DB_DRIVERS = (
    "asyncpg",
    "psycopg",
    "psycopg2",
    "psycopg_pool",
    "aiopg",
    "sqlalchemy",
    "databases",
    "pgcommon",
)
# Rule 3: no worker code touches events, SQS, SNS, the outbox or dedup. The engine writes the outbox in
# the same transaction; the relay runs in the API pods.
EVENT_AND_AWS = ("eventcommon", "boto3", "botocore", "aioboto3", "aiobotocore")
OTHER_CLIENTS = ("qdrant_client", "httpx", "requests", "aiohttp", "redis")
FORBIDDEN_CLIENTS = (*DB_DRIVERS, *EVENT_AND_AWS, *OTHER_CLIENTS)
# Rule 1: logs, metrics and traces go through platform-fastapicommon only.
DIRECT_OBSERVABILITY_SDKS = ("logging", "structlog", "prometheus_client", "opentelemetry")
STDLIB = set(__import__("sys").stdlib_module_names)


def imports(path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
            out += [f"{node.module}.{a.name}" for a in node.names]
    return out


def files(sub: str) -> list[Path]:
    return sorted((SRC / sub).rglob("*.py"))


def test_wd1_workflows_import_only_stdlib_temporal_and_plain_types() -> None:
    for f in files("workflows"):
        for mod in imports(f):
            top = mod.split(".")[0]
            ok = (
                top in STDLIB
                or top == "temporalio"
                or mod.startswith("llm_models")
                or mod == "__future__"
                or mod == "llm_workers_generation"
                or mod.startswith(SANDBOX_SAFE_OWN)
            )
            assert ok, f"{f.name} imports {mod}"


def test_wd2_wd3_no_database_s3_http_or_sql_anywhere() -> None:
    for f in SRC.rglob("*.py"):
        for mod in imports(f):
            assert mod.split(".")[0] not in FORBIDDEN_CLIENTS, f"{f.relative_to(SRC)} imports {mod}"
        assert not re.search(
            r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET)\b",
            f.read_text(),
            re.I,
        ), f"raw SQL in {f.name}"


def test_wb1_only_engine_api_imports_the_engine() -> None:
    for f in SRC.rglob("*.py"):
        for mod in imports(f):
            if mod.split(".")[0] == "rag":
                assert f.name == "engine_api.py", f"{f.relative_to(SRC)} imports {mod}"
                assert mod.startswith(("rag.engine", "rag.infrastructure.config")), (
                    mod
                )  # public API + build_engine's argument
    # and nothing reaches the engine's internals through the public module
    engine_api = SRC / "activities" / "engine_api.py"
    assert not [
        m
        for m in imports(engine_api)
        if m.startswith("rag.") and not m.startswith(("rag.engine", "rag.infrastructure.config"))
    ]


def test_wd4_engine_never_imports_this_package() -> None:
    sibling = Path(__file__).parents[4] / "llm-retrieval-generation" / "src" / "rag" / "engine"
    if sibling.exists():
        for f in sibling.rglob("*.py"):
            assert not any(m.startswith("llm_workers") for m in imports(f))
            assert "temporalio" not in imports(f) and not any(
                m.startswith("temporalio") for m in imports(f)
            )  # WB-7


def test_rule1_no_module_imports_a_logging_metrics_or_tracing_sdk_directly() -> None:
    """WD-5. No allow-list: worker.py logs through ``fastapicommon.logging.get_logger`` too.

    ``temporalio.contrib.opentelemetry`` (the tracing interceptor) is a ``temporalio`` import, not an
    ``opentelemetry`` one; the tracer provider comes from ``fastapicommon.telemetry``.
    """
    for f in SRC.rglob("*.py"):
        for mod in imports(f):
            assert mod.split(".")[0] not in DIRECT_OBSERVABILITY_SDKS, (
                f"{f.relative_to(SRC)} imports {mod}"
            )


def test_rule1_workflow_code_never_imports_fastapicommon() -> None:
    """Workflows run in Temporal's sandbox (WD-1): they keep ``temporalio.workflow.logger``."""
    for f in files("workflows"):
        for mod in imports(f):
            assert mod.split(".")[0] != "fastapicommon", f"{f.name} imports {mod}"


def test_rule1_workflow_side_metrics_module_stays_sandbox_safe() -> None:
    """``observability/metrics.py`` is imported by workflows: stdlib and temporalio only, never fastapicommon.

    Named exceptions to "metrics only through fastapicommon": Temporal's SDK telemetry (``setup.py``) and
    the workflow-side instruments here (workflow code cannot import fastapicommon).
    """
    for mod in imports(SRC / "observability" / "metrics.py"):
        top = mod.split(".")[0]
        assert top in STDLIB or top in ("temporalio", "llm_workers_generation"), mod


def test_rule1_activity_side_metrics_are_not_imported_by_workflows() -> None:
    for f in files("workflows"):
        for mod in imports(f):
            assert "app_metrics" not in mod, f"{f.name} imports {mod}"


def test_rule1_no_hand_rolled_http_server_and_uvicorn_only_in_the_ops_module() -> None:
    for f in SRC.rglob("*.py"):
        text = f.read_text()
        assert "http.server" not in text and "asyncio.start_server" not in text, f.name
        if f.name != "ops.py":
            assert not any(m.split(".")[0] == "uvicorn" for m in imports(f)), f.name


def test_rule1_the_process_logs_through_fastapicommon() -> None:
    assert "from fastapicommon.logging import get_logger" in (SRC / "worker.py").read_text()
    assert "configure_logging" in (SRC / "observability" / "setup.py").read_text()


def test_rule2_the_settings_have_no_database_or_pool_setting() -> None:
    """The engine builds its pools from its own settings; this repo's ``Settings`` has none."""
    names = [
        n.target.id
        for n in ast.walk(ast.parse((SRC / "config.py").read_text()))
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    ]
    for name in names:
        assert not any(w in name for w in ("database", "dsn", "pool", "pg_", "postgres")), name


def test_rule3_no_worker_code_names_events_the_outbox_or_dedup() -> None:
    # the substrings of the event path; the engine owns all of it
    needles = ("sqs", "sns_", "topic_arn", "outbox", "processed_events", "relay", "envelope")
    for f in SRC.rglob("*.py"):
        text = f.read_text().lower()
        for needle in needles:
            assert needle not in text, f"{f.relative_to(SRC)} mentions {needle}"


def test_the_blocked_import_lists_agree() -> None:
    """``_guard._STORE_MODULES`` classifies store exceptions; every one of them must also be blocked."""
    from llm_workers_generation.activities._guard import _STORE_MODULES

    assert set(_STORE_MODULES) <= set(FORBIDDEN_CLIENTS)
    assert len(set(FORBIDDEN_CLIENTS)) == len(FORBIDDEN_CLIENTS)  # no entry twice


def test_wd7_activity_and_workflow_modules_are_confined() -> None:
    for f in SRC.rglob("*.py"):
        rel = f.relative_to(SRC)
        text = f.read_text()
        if "from temporalio import activity" in text or "temporalio.activity" in text:
            assert rel.parts[0] == "activities", f"{rel} uses temporalio.activity"
        if "from temporalio import workflow" in text or "temporalio.workflow" in text:
            assert rel.parts[0] == "workflows", f"{rel} uses temporalio.workflow"


def test_no_langgraph_checkpointer_is_installed() -> None:
    text = (SRC / "graphs" / "section_graph.py").read_text()
    for saver in ("MemorySaver", "SqliteSaver", "PostgresSaver", "InMemorySaver"):
        assert saver not in text
    assert "g.compile()" in text and "checkpointer=" not in text


def test_workflows_use_no_clock_randomness_or_threads() -> None:
    for f in files("workflows"):
        text = f.read_text()
        for banned in (
            "datetime.now(",
            "datetime.utcnow(",
            "time.time(",
            "uuid4(",
            "random.",
            "import threading",
            "os.environ",
            "time.sleep",
        ):
            assert banned not in text, f"{f.name} uses {banned}"
