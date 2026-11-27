# Contributing

This is an internal LLM-subsystem service for the XpertPMS platform: the Worker Pool's generation and extraction deployment. This guide covers development setup, how to extend the service, test requirements, and the PR process for `llm-workers`. Read [`README.md`](README.md) for what the service does, [`ARCHITECTURE.md`](ARCHITECTURE.md) for how it is built and [`docs/lld/llm-lld-workers-service.md`](docs/lld/llm-lld-workers-service.md) for why.

## Prerequisites

- Python 3.15+ (matches `requires-python`; ruff and mypy target 3.15). `make setup` lets `uv` fetch it. CI and the image run 3.15.2
- `make`. `uv` (pinned `0.13.6`) is installed into `.venv` by `make setup` if it is not on your `PATH`
- **Read access to the engine repository** over ssh. The engine (`rag.engine` and `llm_models.workers`) is a private git dependency, `BCBP-SOLUTIONS-FZC-LLC/llm-retrieval-generation`, pinned in `generation/pyproject.toml` (`[tool.uv.sources]`). In CI the `ENGINE_READ_KEY` secret provides it
- Network on the first test run: the Temporal test server is downloaded by the SDK
- Docker, only for `make trivy`, `make docker-build` and the pre-push hook (they also need `GO_PRIVATE_TOKEN` or `gh auth login`, for the image build)

## Development setup

```bash
git clone https://github.com/BCBP-SOLUTIONS-FZC-LLC/llm-workers
cd llm-workers
make setup      # .venv + uv sync --locked, copies .env-example to .env, installs the git hooks
make lint       # verify the linter passes
make test       # unit, workflow (time-skipping), replay and security tests
make ci         # the full CI set
docker compose up -d   # a Temporal dev server on :7233 (UI :8233), to run a worker by hand
```

The database, caches and model stub that the engine needs come from the source repository, not from here: `make docker-up` and `make gateway-up` in `llm-retrieval-generation`. The test suites do not need them: they run against `generation/tests/fakes/fake_engine.py`.

`make setup` installs `.githooks/pre-commit`, which runs `fmt-check`, `lint`, `typecheck` and `arch` before every commit, and `lock-check` when `pyproject.toml` or `uv.lock` is staged. `.githooks/pre-push` builds the image and runs `make trivy` (the release CVE gate) before every push; it needs Docker, skips branch deletions, and `git push --no-verify` bypasses it. Re-run `make install-hooks` any time a file in `.githooks/` changes.

## Project layout

```
pyproject.toml, uv.lock          uv workspace root, locked
generation/                      the one member today: package llm-workers-generation
  src/llm_workers_generation/
    workflows/                   GenerationWorkflow, SectionWorkflow, ExtractionWorkflow, TenantSlots (no I/O)
    activities/                  the 15 activities, the EngineApi port, the one module that imports rag.engine
    graphs/                      the LangGraph section graph
    codec/                       payload codec and failure converter
    observability/               metrics, tracing runtime, health probes
    versioning/                  Worker Deployment config, patch registry, replay
    operator/                    audited operator commands
    queues.py, tuning.py, errors.py, types.py, config.py, worker.py
  tests/{unit,workflow,replay,security,integration,contract,fakes}/
deploy/                          Dockerfile and the Helm chart (one Deployment per queue)
docs/                            the LLD, ADRs, module map, LLD traceability
```

`workflows/` may import only the standard library, `temporalio`, `llm_models.workers` and a short list of sandbox-safe own modules (`types`, `tuning`, `errors`, `queues`, `observability.metrics`). That rule and the others in LLD §3.3 are enforced by `generation/tests/unit/test_architecture.py`.

## Extending the service

### Adding or changing an activity

1. Put the body in `activities/generation.py` (or `slots.py`). Open it with `self._run(...)`, which opens `run_scope` first, translates errors once and turns a gone, terminal or quota-refused run into a **result**, never an exception (`activities/_guard.py`).
2. Every table read or write is an `EngineApi` call (`activities/ports.py`). If the engine's public API lacks what you need, **do not reach around it**: record the gap in `docs/adr/0003-engine-api-gaps.md` and raise it for an additive engine change.
3. Arguments and results go in `types.py` and hold identifiers, enums and numbers only. `unit/test_types_and_payloads.py` fails a field that looks like content.
4. Register it in `activities/build.py` (work queue for long activities, control queue for run-state writes).
5. Never rename an existing activity name (WV-2): a new behaviour is a new name.
6. Add the activity to `FakeEngineApi` if it needs a new engine call, and a test in `unit/test_guard_and_activities.py`.

### Changing a workflow

1. Workflow code is replayed from history: no I/O, no wall clock, no randomness, no threads, no environment reads. Time is `workflow.now()` or `workflow.time()`.
2. **A change to the order or number of commands needs a `workflow.patched("<id>")` marker**, registered in `versioning/patches.py` with the date, so histories already running replay unchanged (WV-1). `make replay` must stay green.
3. Workflow-affecting numbers (timeouts, bounds, the reconcile interval) go in `tuning.py`, not in the environment (ADR-0004).
4. Add a time-skipping test in `generation/tests/workflow/` for each new path. Remember that a wait only skips while the test awaits the workflow's result; otherwise use `env.sleep(...)`.
5. Every wait reads the run's row on a signal or on a timer; a signal is only a hint (WW-1).

### Adding a signal or a run-input field

These are contracts with the API service. Read `docs/worker-pool-contract.md`; the plain types (`llm_models.workers`) are the engine distribution's, so a new field is an engine change first. Handlers only record; the loops decide. Duplicate and stale signals must be no-ops (WG-1..WG-3).

### Adding an environment variable

1. Add a typed field to `Settings` in `generation/src/llm_workers_generation/config.py`; the variable is the upper-cased field name. Give it a range check and, if it must agree with another setting, a rule in `_validate`.
2. Add it to `.env-example` with a comment, and to the Helm `values.yaml` and `templates/deployment.yaml` if a pod needs it.
3. If a workflow reads the value, it is not an environment variable: see ADR-0004.

### Adding a metric

Where is it recorded? **From an activity or the worker process:** add it to `observability/app_metrics.py` as a fastapicommon collector (`new_counter` ends `_total`, `new_histogram` ends `_seconds`, a gauge never ends `_total`; the name starts `llm_workers_`; the library refuses a wrong suffix or an id-like label). A quantity that is not seconds (tokens, cost) is a counter, not a histogram. **Only inside workflow code:** first ask whether an activity at that moment can record it; if not, add it to `observability/metrics.py` (Temporal's meter), which must stay sandbox-safe and is a named exception (ADR-0006). Never import fastapicommon under `workflows/`. Labels come from the allow-list only: **no tenant, user or document id** (OBS-2); `unit/test_metrics.py` rejects it and checks each instrument is accounted for once. Emit it where the event happens, and update `docs/lld-traceability.md` if the built count changes.

### Changing the engine pin

Edit `[tool.uv.sources]` in `generation/pyproject.toml`, run `make lock`, then `make arch`: the engine contract test compares the installed engine's own snapshot with `generation/tests/contract/engine_api_v1.json`. Update that file only with the new tag's snapshot, in the same change. Update `docs/adr/0003-engine-api-gaps.md` if a gap closed.

## Testing requirements

| Layer | Location | Notes |
|-------|----------|-------|
| Unit | `generation/tests/unit/` | Selectors, settings, codec, metrics, the section graph, activities and error translation over the fake engine, architecture rules, engine contract |
| Workflow | `generation/tests/workflow/` | Temporal time-skipping test server: the gate, signals, quota in each phase, restart, cancel, breaker, extraction, `TenantSlots` |
| Replay | `generation/tests/replay/` | Recorded histories of every workflow type replay against the current code (WV-3); one test proves a non-deterministic change is caught |
| Security | `generation/tests/security/` | A canary string must not reach any history, log line or signal (WS-9) |
| Integration | `generation/tests/integration/` (`-m integration`) | Worker bootstrap, the encrypting converter, operator commands against a local Temporal dev server |
| Contract | `generation/tests/unit/test_engine_contract.py`, `test_real_engine.py` | The engine's recorded API shape; the adapter against the installed engine |

`make test` runs everything except the `integration` marker; `make test-ci` runs all of it with coverage. The suites run against the fake engine; the two contract tests run the real engine in a subprocess and skip when it is not installed. `make real-engine-test` (Docker, not in `make ci`) runs the adapter and activities against the real engine on throwaway Postgres, S3 and Valkey (`generation/real_engine/`, about 6 s once the images are pulled; it clones the engine tag into `.cache/engine-tag` for the migrations).

### Canonical test cases

Do not weaken or delete these without a design discussion:

- `unit/test_architecture.py`: WD-1..WD-7 and WB-1.
- `security/test_no_content_in_history.py`: nothing readable in history, failures, signals or logs.
- `replay/test_replay.py`, including `test_a_nondeterministic_change_is_caught`.
- `unit/test_engine_contract.py` and `unit/test_real_engine.py`: the engine boundary against the installed engine (1.1.0): the shape snapshot, the pack fields, the exported types, the `failed` edges and gap 6 (the engine's metrics are refused under the workers' identity: when that test starts failing, engine 1.2.0 was adopted, so update ADR-0003 and the metrics switch).
- `real_engine/test_adapter_on_the_real_engine.py`: the adapter on real Postgres (RLS as a non-superuser, `fail_run` edges, the text round trip through S3, `ContentUnavailable`, tenant isolation). Do not replace its stores with doubles.
- `workflow/test_generation_flow.py::test_cancel_during_stage_2...`: a cancelled run is never written and its slots are freed.

### Coverage gate

`make test-ci` writes `coverage.xml`. CI fails below **95%** (`.github/scripts/coverage-gate.sh`, `COVERAGE_THRESHOLD`); coverage was 96.4% at the last run. A test that only touches a line without asserting behaviour does not count (LLD §18.9).

## Linting

```bash
make lint        # ruff check + ruff format --check
make typecheck   # mypy --strict on generation/src
make fmt         # ruff --fix and format
```

`ruff` selects `E F I UP B D ANN ASYNC` (line length 100): every public function in `src/` has a type annotation and a docstring. Tests are exempt from `D`, `ANN` and `E501`.

## Guardrails and forbidden patterns

- **Reach data only through `rag.engine`.** No SQL, `asyncpg`, `boto3`, `qdrant_client` or raw HTTP client. Only `activities/engine_api.py` imports `rag`.
- **No content in Temporal history.** Identifiers, enums and counts in every argument, result, signal, failure and heartbeat. Raise `safe_error(code, ...)` (fixed text, no details), never an exception built from engine text.
- **No I/O in workflow code**, and a patch marker for any command change.
- **Never rename an activity, signal or workflow.**
- **No LangGraph checkpointer**, and no text in graph state or in a checkpoint row.
- **Logs and traces through `platform-fastapicommon` only** (`get_logger`; never `logging`, `structlog`, `prometheus_client` or `opentelemetry`). Workflow code is excluded: it keeps `temporalio.workflow.logger` and never imports fastapicommon. No worker code imports a database driver or `pgcommon`, or touches `eventcommon`, SQS, SNS, the outbox or dedup (`make arch`).
- **Never log or label content**, and never use a tenant, user or document id as a metric label.
- **Fail closed.** A step that cannot read scope or the pack ends with a stated reason, never a default.
- **Never add a dependency without a lockfile update.** `make lock`, commit `uv.lock`; CI runs `make lock-check`.
- **Do not add `# noqa` or `# type: ignore` without a reason on the same line.**
- **Do not commit `.env`.** `.env-example` is the only committed template.

## Documentation update checklist

| What changed | Documents to update |
|---|---|
| A rule's status (built, partial, not built) | `docs/lld-traceability.md` (and its score table) |
| New or changed deviation, engine gap or assumption | The matching ADR in `docs/adr/`; `docs/worker-pool-questions-for-teams.md` if another team must answer |
| New environment variable | `config.py` · `.env-example` · Helm `values.yaml` · README if it changes how you run a worker |
| New activity, workflow, queue or metric | `docs/module-map.md` · `docs/lld-traceability.md` · `ARCHITECTURE.md` if the shape changed |
| Engine pin or contract snapshot | `generation/pyproject.toml` · `generation/tests/contract/engine_api_v1.json` · `VERSIONING.md` · ADR-0003 |
| Release, image or workflow change | `VERSIONING.md` (including its *Planned* table) · this file's Deployment section |
| Anything an operator must react to | A runbook (none are written yet: LLD §15.6) |

## Branch naming

- `feat/<short-topic>` — new capability
- `fix/<short-topic>` — bug fix
- `docs/<short-topic>` — documentation-only change
- `chore/<short-topic>` — dependencies, tooling, CI, refactors with no behavioural change

Base new branches off the latest `main`.

## Pull request checklist

- [ ] `make ci` passes locally: `lock-check`, `fmt-check`, `lint`, `typecheck`, `arch`, `replay`, `metrics-lint`, `chart-check` (needs `helm` and `kubeconform`), `test-ci` and `build`, mirroring CI
- [ ] `make cover-check` if you added or removed code in a thinly covered module (the gate is 95%)
- [ ] New public functions have type annotations and docstrings
- [ ] New behaviour is covered by tests in the right layer; a workflow change has a time-skipping test and a passing replay
- [ ] A workflow command change has a `workflow.patched` marker registered in `versioning/patches.py`
- [ ] Nothing readable can reach Temporal history (the security test is green and, if you added a field or an error path, covers it)
- [ ] `CHANGELOG.md` *Unreleased* updated (`.github/workflows/changelog-check.yml` fails PRs that touch `generation/src/` or `deploy/helm/` without an entry)
- [ ] `uv.lock` updated if dependencies or the engine pin changed (`make lock-check`)
- [ ] Documentation update checklist above reviewed
- [ ] Contract changes called out in the PR description (workflow, signal or activity name, run input, queue, metric name, env var) so [`VERSIONING.md`](VERSIONING.md)'s MAJOR/MINOR rules are applied

## Commit style

Keep messages concise; describe the *why*. Reference the LLD section or rule id when it clarifies the change:

```
fix(workflows): resolve a lost approve from the row on the next reconcile (WG-7)
feat(activities): treat a quota refusal in capture_facts as a result (WA-17)
fix(slots): build TenantSlots state in @workflow.init so update-with-start finds its queue
docs: record the engine's missing suspended -> failed edge in ADR-0003
test(security): add a canary case for a failure raised inside the section graph
```

## Deployment

**Partly planned.** A `Dockerfile` exists (`deploy/Dockerfile`; `make docker-build`), a Helm chart exists in `deploy/helm/llm-workers/` (one Deployment per queue, KEDA scaler, PDB, network policy; rendered and linted, never applied to a cluster), and `.github/workflows/release.yml` builds, scans, smoke-tests, pushes and signs the image on a version tag (never run; no tag exists). CI validates and builds; it publishes an image only on a push to `main`, and nothing deploys it.

**On push to `main` and on pull requests** (`.github/workflows/ci.yml`, skipped for Markdown-only changes):

1. `validate-test` (`Validate / Test`): `make replay`, `make test-ci`, then the 95% coverage gate.
2. `validate-quality` (`Validate / Quality`): lock check, `uv sync --locked`, `uv pip check`, format, lint, `mypy --strict`, `make arch`, `pip-audit`.
3. `build-image` (`Build image (cache)`: Hadolint, then the cached build), then `trivy` (`Trivy CVE scan`, CRITICAL/HIGH with a fix fails) and `smoke` (`Smoke tests`: non-root, imports the worker and the engine, no compiler).
4. `pr-summary` posts the results on pull requests; `push` (`Push image -> GHCR`) signs and pushes on `main`.

Both reusable workflows and every image build need the `GO_PRIVATE_TOKEN` secret: the same name the IAM repo (`iam-org-membership`) uses, a token with read access to the organisation's private repositories (here the engine, `llm-retrieval-generation`). CI rewrites the engine's ssh address to https and authenticates with it; the Docker build receives it as a BuildKit secret (`gh_token`), never in a layer. The image is **not booted** against a database in CI: a worker needs Postgres and Temporal, which the source repo's compose stack provides.

Notify the platform team and the Workflow Engine team if a change needs a new task queue, a new Worker Deployment name, a search attribute, or a worker build that must stay alive for pinned runs.
