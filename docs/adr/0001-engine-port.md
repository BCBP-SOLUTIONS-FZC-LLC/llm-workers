# ADR-0001: the engine behind a port; one module imports `rag.engine`

**Status:** accepted. **LLD:** §4 (LW-1, WB-1..9), §3.3 (WD-2, WD-3).

**Context.** Every table write, search and model call is the engine's (`rag.engine` 1.1.0). The LLD forbids
SQL, `asyncpg`, `boto3` and `qdrant_client` here, and forbids importing engine internals.

**Decision.** Activities depend on a `Protocol`, `EngineApi` (`activities/ports.py`). `RagEngineApi`
(`activities/engine_api.py`) is the only module that imports `rag.engine`; the test suite's `FakeEngineApi`
is the second implementation. An architecture test fails the build if any other module imports `rag`.
The single internal import is `rag.infrastructure.config.Settings`, the argument type of `build_engine`.

**Consequences.** Activities, the graph and the workflows are tested without a database. The contract test
(`test_engine_contract.py`) compares the real engine with its snapshot and keeps the fake honest.
