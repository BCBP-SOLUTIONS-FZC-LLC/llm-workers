# ADR-0006: environment and deviations to know about

* **Python and the real engine:** the suite, `mypy --strict` and `ruff` (the project's full rule set) were run on
  **CPython 3.15.0 with the real engine wheel installed** (`uv sync`), and the unit and workflow suites also
  pass on 3.14.6 against the fake engine. Two real-engine tests run in a subprocess:
  `test_engine_contract.py` (the engine's own snapshot equals the recorded one) and `test_real_engine.py`
  (every delegated function exists with the parameters the adapter passes; the engine 1.1.0 additions, the
  `failed` edges of the run state machine and the gap-6 `ValueError` are asserted against the installed engine).
* **Control worker and deployments:** both workers of a pod pass the same `WorkerDeploymentConfig`, so
  `<queue>` and `<queue>-control` are queues of one deployment version and a pinned run's control-queue
  activities run on its build (WV-5). The test server does not exercise Worker Versioning: **confirm with
  the Workflow team (PL-6)** that the server supports it for both queues; otherwise use build-id sets (§5.5).
* **`uv.lock` is committed.** `generation/pyproject.toml` pins the engine by the annotated tag
  `engine-v1.1.0` (tag object `047d834`, peeling to `b6b92e6`, the squash merge on the engine's `main`);
  `uv.lock` locks `b6b92e67031fa0f0e39656923c0f73682aea0ba0`. Never pin `3b93dac`: it is the branch commit the
  squash merge orphans. An earlier pin (`ac0a391`) was on a squashed branch too. Re-run `uv lock` when the tag
  moves (ADR-0003).
* **Observability: logs, traces and the workers' own metrics go through `platform-fastapicommon`**
  (`get_logger`, `configure_logging`, `init_tracing`, `init_metrics`, `new_counter`, `new_histogram`), with
  **two named exceptions**:
  1. **Temporal SDK telemetry.** The Temporal `Runtime` with `PrometheusConfig` still serves the SDK's own
     metrics (backlog, schedule-to-start, task failures) on `METRICS_BIND_ADDRESS` (9464). The SDK emits them
     itself; fastapicommon cannot.
  2. **Workflow-side metrics.** Workflow code runs in Temporal's sandbox and may not import fastapicommon
     (WD-1), so the 9 instruments that can only be recorded inside a workflow stay on `workflow.metric_meter()`
     and are served on that same Temporal endpoint: `workflow_started_total`, `workflow_completed_total`,
     `workflow_duration_seconds`, `workflow_history_events_total`, `fanout_in_flight`,
     `reconcile_recovered_total`, `run_deadline_total`, `poison_breaker_total`, `unrecorded_failures_total`
     (`observability/metrics.py`).
  Everything recorded from an activity or the worker process (11 instruments, `observability/app_metrics.py`)
  is a fastapicommon Tier 3 collector named `llm_workers_*`, served by the ops server's `/metrics`.
* **Scrape targets: open decision.** Two endpoints exist: the ops server (`HEALTH_PORT`, 8080, `/metrics`:
  fastapicommon, the activity-side metrics and `platform_*`) and the Temporal runtime (`METRICS_BIND_ADDRESS`,
  9464: SDK telemetry and the 9 workflow-side metrics). Options, for the platform team to choose between
  (does it scrape, or take OTLP?): **(A) two scrape targets**: two endpoints in a `PodMonitor`; nothing to
  build, but a dashboard joins two jobs. **(B) one scrape target**: the ops `/metrics` also relays Temporal's
  endpoint (an HTTP fetch and concatenation; needs an HTTP client, which the architecture rules block today, so
  it is a deliberate exception). **(C) OTLP**: the Temporal runtime pushes with `OpenTelemetryConfig`; the
  fastapicommon collectors are Prometheus-only, so they would still need a scrape. The chart implements A
  for now: named ports `ops` (8080) and `sdk-metrics` (9464) and a PodMonitor with two `/metrics` endpoints
  (`templates/monitoring.yaml`, `podMonitor.enabled`, **off by default**: it needs the Prometheus Operator CRDs, so turn it on per environment). The
  `prometheus.io/*` annotations are dropped: they take one port.
* **The ops server** (`observability/ops.py`) replaces the stdlib probe server: a FastAPI app with
  fastapicommon's health and metrics routers, served by `uvicorn.Server.serve()` as one task in the worker's
  loop. uvicorn's `serve()` installs its own SIGTERM and SIGINT handlers and re-raises the signal on exit,
  which would bypass the graceful drain, so `capture_signals` is overridden to a no-op (pinned by the SIGTERM
  test). Shutdown order: `/ready` 503, workers drain, ops server, engine, tracer.
* **Histogram naming rule** (`fastapicommon/metrics/registry.py::suffix_error`): counters end `_total`,
  histograms end `_seconds`, gauges never end `_total`. A Tier 3 token or cost histogram cannot exist, so
  `tokens_per_section` became the counter `section_tokens_total` (sum per stage and tier) and
  `cost_per_document` is not built (it also has no price source in the worker). The rule applies on
  Temporal's meter too (the linter checks that endpoint's `llm_workers_*` families), so the history length is
  the counter `workflow_history_events_total` (events in the histories of ended workflows; divide by
  `workflow_completed_total` for the mean). An earlier version of this ADR said the rule did not apply there:
  that was wrong.
* **One metrics identity per process.** fastapicommon checks every Tier 3 name against `<domain>_<service>_`
  (`llm_workers_`). The engine's own metrics are named `llm_retrieval_generation_*`, so building them under the
  worker's identity raises `ValueError` (pinned by `test_real_engine.py`). `build_rag_engine` therefore passes
  `enable_metrics=False` to the engine: **the engine's own metrics (retrieval stages, verification, cache) are
  off inside the workers**; its logs and traces stay on. Decision for the platform or the engine owner.
* **`APP_ENV` must be one of `local`, `dev`, `test`, `staging`, `prod`** when metrics are on (the platform
  registry's vocabulary): `production` fails the process at start. The Helm default is now `prod`.
* **Not built, no truthful event:** `runs_suspended`, `runs_awaiting_gate` and `tenants_waiting` are state
  gauges (an up-down count across pods) with no event at the moment the state ends, `load_shed_steps` has no
  controller, and `cost_per_document` has no price source. They are not declared. Each needs a sweep job or
  a feature, not an `inc()` call.
* **Logging and tracing:** the earlier stdlib `logging` deviation is gone. `init_observability` calls
  `configure_logging` and, where an exporter endpoint is set, `init_tracing`. Workflow code keeps the Temporal
  SDK's workflow logger (WD-1). The Temporal core's own Rust logs do not pass through it.
* **Not built (LLD says so too):** zombie sweep (WOQ-14), freeze pause (WOQ-18), bulk cancel (WO-3),
  residency re-read per section (WR-4), `x-plan` refusal (WQ-7, hook only), load-shedding by tier
  (no controller), the codec server, KEDA scaler address, network-policy peers.

## Observability Standard (workers LLD rev 0.7)

The Enterprise Platform Observability Standard is applied to both endpoints. **Evidence is the platform linter
run over the real scrape of a worker on a dev server**, not names read in source:
`generation/tests/integration/test_metrics_lint.py` (`make metrics-lint`, also in `make test-ci` and in
`validate-test.yml`) starts `worker.run`, drives the paths that emit each metric, scrapes `HEALTH_PORT` and
`METRICS_BIND_ADDRESS`, and runs `fastapicommon-metricslint check` and `check --strict` over each.

**Classification.** Everything is Tier 3 (`llm_workers_<metric>`; the domain `llm` is in the registry's
domain list, and no `llm_*` or `temporal_*` metric is registered). Nothing here has an identical meaning in a
second domain (no `platform_*`), and the two candidates for a domain-shared `llm_*` name are requests to the
platform team, not adopted (`docs/observability-registry-requests.md`). No code uses a `platform_*` or
`llm_*` name of its own. `platform_dependency_request_seconds` is not used: workers call dependencies through
the engine.

| Metric | Recorded in | Type | Labels (values; bound) |
|---|---|---|---|
| `activity_duration_seconds` | activity | histogram | `activity` (the 15 activity names; 15) |
| `activity_failures_total` | activity | counter | `activity`, `error_type` (the §4.4 error types; 8) |
| `section_duration_seconds` | activity | histogram | `stage` (`stage1`, `stage2`; 2) |
| `section_outcome_total` | activity | counter | `stage`, `result` (`complete`, `gap`, `failed`; 3) |
| `slot_wait_seconds` | activity | histogram | `tier` (`standard`, `enterprise`, `strict`; 3) |
| `quota_suspensions_total` | activity | counter | `stage` (`outline`, `stage1`, `stage2`, `coherence`, `extract`, `unknown`; 6) |
| `run_gone_total` | activity | counter | `activity` (15) |
| `gate_expired_total` | activity | counter | none |
| `scope_revoked_total` | activity | counter | none (domain-shared candidate) |
| `section_tokens_total` | activity | counter | `stage` (2), `tier` (3) |
| `retried_tokens_total` | activity | counter | `tier` (3) |
| `workflow_started_total` | workflow | counter | `kind` (`document`, `extraction`; 2) |
| `workflow_completed_total` | workflow | counter | `kind` (2), `status` (`complete`, `failed`, `cancelled`, `gone`; 4) |
| `workflow_duration_seconds` | workflow | histogram | `kind` (2) |
| `workflow_history_events_total` | workflow | counter | `kind` (2) |
| `fanout_in_flight` | workflow | gauge | `scope` (`document`; 1) |
| `reconcile_recovered_total` | workflow | counter | `wait` (`gate`, `suspended`; 2) |
| `run_deadline_total` | workflow | counter | none |
| `poison_breaker_total` | workflow | counter | `error_type` (the failure reasons; 9) |
| `unrecorded_failures_total` | workflow | counter | none |

The three constant labels `domain`, `service`, `environment` come from one function
(`observability/setup.py::platform_labels`): `init_metrics` stamps them on the ops endpoint and the Temporal
runtime's `global_tags` stamp them on every series of the 9464 endpoint, so instrumentation cannot omit or
misspell them (naming rule 8). A real scrape shows them on every `llm_workers_*` and `temporal_*` series.
No label is an id of a tenant, user, run, section, event or request (OBS-2); the label names are none of the
registry's prohibited names or suffixes; the registry's `outcome` label is **not** used (see below).

**Not built (each with its reason, none declared), and the name each would get:** `runs_suspended` (state
gauge: no event when the state ends; `llm_workers_runs_suspended`), `runs_awaiting_gate` (same;
`llm_workers_runs_awaiting_gate`), `tenants_waiting` (same, label `tier`; `llm_workers_tenants_waiting`),
`load_shed_steps` (no controller; gauge `llm_workers_load_shed_steps`, label `tier`), `cost_per_document` (no
price source, and a Tier 3 histogram must end `_seconds` or `_bytes`: a counter
`llm_workers_document_cost_units_total` once a price source exists).

**What the real scrapes showed, and what was changed:**

* `section_outcome_total` used the label `outcome` with the values `complete`, `gap` and `failed`.
  `outcome` is in the registry's label vocabulary (`success`, `failure`, `error`), and the linter checks
  those values even on a Tier 3 metric. The label is now `result`. **Label contract change:** a dashboard on
  `outcome` must read `result`. Nothing is deployed, so there is no compatibility period.
* `workflow_history_events` (a histogram not ending `_seconds`) became the counter
  `workflow_history_events_total`.
* The workflow-side `llm_workers_*` series carried none of `domain`, `service`, `environment`: now set as
  `TelemetryConfig.global_tags` (temporalio 1.37.0 supports it; checked by scrape). The SDK's `service_name`
  label (`temporal-core-sdk`) is also attached by the SDK (`attach_service_name`) and is left alone.
* `workflow_duration_seconds` records seconds but got the SDK's millisecond-tuned default buckets (50 to
  10000), so every run fell in the first bucket. It now has explicit buckets
  (`histogram_bucket_overrides`, keyed by the full metric name; confirmed by scrape).
* A labelled collector with no series yet has no `service` label, so the linter cannot classify it
  (`llm__<metric>` namespace error). The test drives every activity-side path once so each family has a
  series. This is a linter limitation, recorded as a question to the platform team (PL-13).
* **Not changed, options for the user:** `PrometheusConfig.durations_as_seconds` (SDK latencies in seconds
  instead of milliseconds), `unit_suffix` and `counters_total_suffix`. The last two rename the SDK's metrics, so
  they are off; `durations_as_seconds` changes the SDK's units and is the user's decision.

**Named exception: the SDK's own telemetry (`temporal_*`).** All 21 families the Temporal runtime serves
(`temporal_activity_execution_failed`, `temporal_request`, `temporal_num_pollers`,
`temporal_workflow_task_schedule_to_start_latency`, ...) are reported by the linter as `[namespace]` errors
(unclassified name): `temporal_` is not an exempt prefix (the registry exempts `go_`, `process_`, `promhttp_`
and `python_`). They are not renamed and not silenced by a flag or a filter. The test pins the exception: on
`check` and `check --strict` the findings on that endpoint must be exactly the `temporal_*` families, all of
rule `namespace`, and nothing of ours. Several are also not `_total` counters and not `_seconds` histograms
(the SDK's latencies are milliseconds), so exempting the prefix is the clean answer. **Question for the
registry owners (the platform team): PL-12, add `temporal_` to `exempt_prefixes`.**

**`--strict` on the ops endpoint.** Strict makes the platform's own still-`proposed` `platform_*` metrics errors
(16 of them, owner `platform-gincommon`, registered by the library at start). The test runs strict with
`--allow-proposed-owner platform-gincommon` (strict about what this service emits, not about the library's)
and asserts that without the flag the only errors are `[status]` on `platform_*` names. This uses a flag the
library documents for this case; it differs from the SDK rule above, and is recorded for the user to confirm.

**Every built metric is exercised in the real scrape** (20 of 20), including the three the linter had not seen
in the first run: `reconcile_recovered_total` (a lost approve signal, with `WAIT_RECONCILE_SECONDS` patched to 2 s),
`poison_breaker_total` (`POISON_MIN_SECTIONS` patched to 2) and `unrecorded_failures_total`. The last lost its
old trigger when engine 1.1.0 added `suspended -> failed`: the 30-day suspension timeout is now recorded. It
still fires when the terminal write itself cannot be recorded for its whole window, that is a database outage
while `fail_run` runs (60 minutes, `TERMINAL_WRITE_RETRY_MINUTES`, patched to 0.1 in the test). That is the one
path that remains, and `workflow/test_failure_paths.py::test_unrecorded_failure_is_retried_then_fails_the_workflow_visibly`
pins it on the time-skipping server.

**Artefacts the Standard also covers, none written yet** (named, with the metric each would read):

* Dashboards: `llm-workers-overview` (`workflow_started_total`, `workflow_completed_total`,
  `workflow_duration_seconds`), `llm-workers-sections` (`section_duration_seconds`, `section_outcome_total`,
  `slot_wait_seconds`), `llm-workers-quota-and-gate` (`quota_suspensions_total`, `gate_expired_total`),
  `llm-workers-tokens` (`section_tokens_total`, `retried_tokens_total`), `llm-workers-temporal` (the
  `temporal_*` SDK series).
* Alert rules (LLD §15.4, 17 rows): queue backlog age and schedule-to-start (SDK series); control queue backlog,
  workflow task failures (SDK); unrecorded failures (`unrecorded_failures_total`); run deadline
  (`run_deadline_total`); poison breaker (`poison_breaker_total`); workflow history above 10,000
  (`workflow_history_events_total` over `workflow_completed_total`); suspended runs (needs the unbuilt
  `runs_suspended`); gate expiries (`gate_expired_total`); lost signals (`reconcile_recovered_total`); SLO burn;
  the rest read the API service, the outbox, certificates or pod restarts.
* Recording rules: `llm_workers:section_p95_seconds`, `llm_workers:workflow_history_events_mean`,
  `llm_workers:section_failure_ratio`.
* SLO definitions (LLD §15.5, four): section completes within 3 minutes at p95
  (`section_duration_seconds`); first progress within 10 seconds (stated at the outbox, not a metric here); a
  queued task waits no longer than 5 minutes (SDK `temporal_*_schedule_to_start_latency`); a document completes
  (`workflow_duration_seconds`).
* **HPA references: none.** The chart has no HPA. It has a KEDA Temporal scaler
  (`autoscaling.kedaTemporalScaler`, `queuedActivitiesPerPod`) that scales on task-queue backlog read from
  Temporal, and uses no metric from this repository.
* Runbooks (LLD §15.6, 16, in `docs/runbooks/`): a stuck queue, a non-determinism failure after a deploy, an
  unrecorded failure, a quota suspension storm, a Gateway outage, a database outage, a lost workflow (W-8), a
  quiet async run, a lost signal, a worker rollback, a strict-residency capacity shortage, a purge during a
  run, a tenant freeze, a poison-section breaker, an expiring Temporal certificate, a payload-codec key
  rotation. The inventory of the platform registry is generated (`make metrics-inventory`,
  `docs/platform-observability-inventory.md`); it lists the platform's metrics, not this service's, which are
  the table above. `fastapicommon-metricslint refs` is not run until dashboards exist.
* **Skipped:** the legacy-metric migration and sunset steps. Nothing is deployed, so no old and new metric run
  in parallel.
