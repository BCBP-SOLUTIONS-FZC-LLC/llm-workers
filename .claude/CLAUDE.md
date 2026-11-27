# CLAUDE.md
Guidance for Claude Code working in **llm-workers** (the Worker Pool of the LLM subsystem, Tender Management SaaS platform). The design is `docs/lld/llm-lld-workers-service.md` (rev 0.11, based on HLD v0.31). **This file is the source of truth for the Worker Pool design: change the design here, in this repo.** The sibling LLD (`llm-retrieval-generation`, rev 2.144) is cited by section number only. Where it and the LLM HLD disagree, the HLD is authoritative. Where each LLD rule is built and tested: `docs/lld-traceability.md`. Decisions, deviations and engine gaps: `docs/adr/` (read ADR-0003 and ADR-0006 first).

## What this repo is
The generation and extraction deployment of the Worker Pool: **durable Temporal workflows** (`GenerationWorkflow`, `SectionWorkflow`, `ExtractionWorkflow`, `TenantSlots`), their activities, a **LangGraph section graph**, quota-aware pause and resume, the payload codec and failure converter, worker bootstrap, and metrics. It is a `uv` workspace: member `generation`, package `llm-workers-generation`, code in `generation/src/llm_workers_generation/`. Python 3.15.

**It does not own:** API routes, admission or request validation (the API service, `llm-retrieval-generation`, which starts and signals workflows); the database schema and migrations (the API service); the engine's building blocks and every table write (`rag.engine`, imported as a pinned package); the ingestion and re-index workflows (Ingestion); model routing, credentials, residency and quota accounting (Model Gateway); the Temporal cluster (the Workflow Engine team); users, tenants and roles (IAM).

## Common commands
```bash
make setup         # .venv, uv sync --locked, .env from .env-example, git hooks (ssh access to the engine repo needed)
make fmt           # ruff autofix + format
make lint          # ruff check + format check
make typecheck     # mypy --strict on generation/src
make arch          # architecture rules (WD-1..7, WB-1) and the engine contract tests
make replay        # recorded workflow histories still replay (WV-3)
make test          # the suite without the integration marker
make test-ci       # everything, with coverage (downloads the Temporal test server on first run)
make cover-check   # test-ci, then the 95% gate
make lock / lock-check
make build         # wheel and sdist into dist/
make ci            # lock-check + fmt-check + lint + typecheck + arch + replay + metrics-lint + chart-check + test-ci + build (chart-check needs helm and kubeconform)
make vuln-check    # pip-audit on the locked deps
make trivy         # build the image (needs Docker and GO_PRIVATE_TOKEN or `gh auth login`), CVE-scan it as release.yml does
```
`docker compose up -d` starts a Temporal dev server only. The database and model stub come from `llm-retrieval-generation` (`make docker-up`, `make gateway-up` there).
One test: `.venv/bin/python -m pytest generation/tests/workflow/test_generation_flow.py::test_gate_approval --no-cov`.

## Rules that must not break
- **Only `rag.engine` writes tables.** No SQL, no `asyncpg`, `boto3`, `qdrant_client` or raw HTTP client anywhere. `activities/engine_api.py` is the one module that imports `rag` (plus `rag.infrastructure.config` for `build_engine`). Never import engine internals (WD-2, WD-3, WB-1).
- **Workflows do no I/O.** `workflows/` imports only the standard library, `temporalio`, `llm_models.workers` and the sandbox-safe own modules. No wall clock, randomness, threads or environment reads; time is `workflow.now()` (WD-1, WDT-1..5). Workflow-affecting numbers live in `tuning.py`, not the environment (ADR-0004).
- **A change to the commands a workflow issues needs a patch marker** (`workflow.patched`, registered in `versioning/patches.py`) and a replay that still passes. Never rename an activity, signal or workflow (WV-1, WV-2).
- **Nothing readable goes into Temporal history.** Workflow input, signals, activity results, failures and heartbeats carry identifiers, enums and counts only: no prompt, chunk, generated text or reviewer's instruction. Errors leaving an activity are `ApplicationError(type, fixed message)` with no details and no chaining (`activities/_guard.py`). `generation/tests/security/test_no_content_in_history.py` is the guard; never weaken it (WS-6, WS-9..WS-11).
- **Rule 1, observability: logs, metrics and traces go through `platform-fastapicommon` only** (WD-5). Log with `fastapicommon.logging.get_logger`; metrics with `new_counter`/`new_histogram` in `observability/app_metrics.py` (counters `_total`, histograms `_seconds`, names `llm_workers_*`); never `logging`, `structlog`, `prometheus_client` or `opentelemetry`. Never a tenant, user or document id as a metric label (OBS-2). **Two named exceptions:** Temporal SDK telemetry (the `Runtime` in `setup.py`), and the 9 workflow-side metrics in `observability/metrics.py`: workflow code runs in the sandbox, never imports fastapicommon (WD-1) and keeps the SDK's workflow logger and `workflow.metric_meter()`. Health and `/metrics` are the ops server (`observability/ops.py`), a FastAPI app on one uvicorn task; never `http.server` or `asyncio.start_server`. **Observability Standard:** every metric is Tier 3 `llm_workers_*`; never add a `platform_*` or `llm_*` name (a shared name needs the platform team's ratification: `docs/observability-registry-requests.md`); never reuse a registry label name (`outcome`, `state`, `reason`, ...) with other values; `domain`, `service` and `environment` come from `observability/setup.py::platform_labels` on both endpoints; any new metric must appear in the real scrape that `make metrics-lint` lints (drive its path in `generation/tests/integration/test_metrics_lint.py`).
- **Rule 2, no database access: no worker code imports a driver or `pgcommon`.** Pools exist only through `rag.engine.bootstrap.build_engine`, built from the engine's own settings; `config.py` has no database or pool setting.
- **Rule 3, no events: no worker code touches `eventcommon`, SQS, SNS, the outbox or dedup.** The engine writes the outbox in the transaction of the state change; the relay runs in the API pods. `make arch` enforces all three rules.
- **A signal is a hint, the run's row is the truth.** Every wait reads `run_state` on a signal or a timer (WW-1).
- **Re-resolve scope per section; a quota refusal is a result, not a failure** (WA-2, WA-17).
- **No LangGraph checkpointer**; graph state carries no text (WA-18, WA-11).
- **Peer contracts are assumptions** until the peer's own document confirms them: cite it, or record the assumption in `docs/worker-pool-questions-for-teams.md` or an ADR. Do not harden a guess into a fact.
- **Keep the docs current in the same change.** Any code change, answer from another team, pin or decision updates the LLD (bump its revision, change-log row and footer counts), `docs/lld-traceability.md`, the questions and contract docs, ADRs, `CHANGELOG.md` and the other docs that state it. Grep for the old wording before finishing.
- **Mark unbuilt things as planned.** `VERSIONING.md` keeps a *Planned* table; `docs/lld-traceability.md` says what is built. Keep both honest.

## Architecture
Follows LLD §3.2, not clean architecture: `workflows/` (plan), `activities/` (work, behind the `EngineApi` port), `graphs/` (section graph), `codec/`, `observability/`, `versioning/`, `operator/`, `queues.py`, `tuning.py`, `config.py`, `worker.py`. See [`ARCHITECTURE.md`](../ARCHITECTURE.md) and `docs/module-map.md`.

## Engine
`rag.engine` (+ `llm_models.workers`) is pinned **by the annotated tag `engine-v1.1.0`** in `generation/pyproject.toml` (the tag peels to `b6b92e6`, locked in `uv.lock`; never pin the orphaned `3b93dac`). Its shape is recorded in `generation/tests/contract/engine_api_v1.json`, byte-equal to the file at the tag. Engine 1.1.0 closed ADR-0003 gaps 1 to 5. **Gap 6 (the engine's metrics identity, engine 1.2.0) is open and not adopted: keep `enable_metrics=False` in `build_rag_engine` and the `ValueError` pin in `test_real_engine.py`.** A pack with no outline for the capability is `PackOutlineMissing`; a failed read of a section's text (`ContentUnavailable`) is retryable and bounded (ADR-0003). The pack keys `outline_skeletons` and `fact_keys` are an assumed Tender contract.

## Claude Code automation in repo
- **Hooks** (`.claude/settings.json`): `format-python.sh` runs `ruff format` on edited project `.py` files (needs `jq` and `make setup`).
- **Skills**: `/pr-check` runs the pre-PR gates; `peer-contract-check` (background) means cite the peer's document or record an assumption.
- **Agents** (read-only): `security-reviewer` (use on changes to activities, the codec, `types.py`, queues or identity), `docs-consistency-reviewer` (give it a diff file).
- **Git hooks**: `.githooks/pre-commit` (lock, format, lint, types, `make arch`) and `pre-push` (`make trivy`; Docker; `--no-verify` bypasses). Install with `make install-hooks`.
- **Branching**: feature branch and PR into `main`; org ruleset requires code-owner approval and the `ci.yml` checks.
