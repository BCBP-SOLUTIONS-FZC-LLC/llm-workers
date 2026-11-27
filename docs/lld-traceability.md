# LLD to code traceability

LLD rev 0.11 (HLD v0.31; engine 1.1.0 adopted, see ADR-0003) (`docs/lld/llm-lld-workers-service.md`). Paths are under `generation/src/llm_workers_generation/`
(code) and `generation/tests/` (tests). Status: **B** built and tested here, **P** partial, **E** owned by the engine or
the API service (not verifiable here), **N** not built.

## Score

| | Rules | B | P | E | N |
|---|---|---|---|---|---|
| Invariants WD to WO (§22.1) | 99 | 68 | 13 | 13 | 5 |

Of the **86 rules this repository owns** (99 minus 13 engine-owned): **68 built and tested (79%)**, **13 partial**,
**5 not built**. Rules are the code-shaped part of the LLD. The operational sections (alerts, SLOs, runbooks, DR
drill, load test, end-to-end, real-database tests) are mostly **not** built; see "Sections without rule ids".
Engine: 1.1.0 is adopted (ADR-0003 gaps 1 to 5 closed); gap 6 (the engine's metrics identity, engine 1.2.0) is open.

## Rules

### WD layer rules (§3.3): 7 B
| ID | St | Where | Test |
|---|---|---|---|
| WD-1 workflows import stdlib, temporalio, plain types | B | `workflows/*` | `unit/test_architecture.py::wd1` |
| WD-2 activities use the public engine API only | B | `activities/engine_api.py` (sole importer; one internal import, ADR-0001) | `::wb1` |
| WD-3 no SQL, asyncpg, boto3, qdrant, HTTP client | B | whole tree | `::wd2_wd3` |
| WD-4 engine never imports workers, no temporalio | B | checked in the sibling repo | `::wd4` |
| WD-5 shared logging, metrics, tracing libs | B, with two named exceptions: Temporal SDK telemetry, and the 9 workflow-side metrics (workflow code cannot import fastapicommon). Logs, traces, the ops server and the 11 activity-side metrics go through `platform-fastapicommon` (ADR-0006) | `observability/{setup,ops,app_metrics,metrics}.py`, `worker.py` | `::rule1_*`, `unit/test_observability_platform.py`, `unit/test_metrics.py`, SIGTERM test |
| WD-6 nothing logs or labels content | B | `observability/metrics.py` label allow-list | `unit/test_metrics.py`, `security/` |
| WD-7 activity and workflow imports confined | B | | `::wd7` |

### WB engine boundary (§4.5): 5 B, 2 P, 1 E, 1 N
| ID | St | Where | Test |
|---|---|---|---|
| WB-1 | B | `activities/engine_api.py` | `test_architecture::wb1` |
| WB-2 pin an exact version | B | `generation/pyproject.toml` pins the annotated tag `engine-v1.1.0` (locked at `b6b92e6`) | `unit/test_engine_contract.py` (the snapshot is byte-equal to the tag's) |
| WB-3 additive changes, 60-day window | E | engine policy | |
| WB-4 snapshot contract test, both repos | B (this repo's half) | | `unit/test_engine_contract.py`, `unit/test_real_engine.py` |
| WB-5 no content to the workflow | B | `types.py` | `unit/test_types_and_payloads.py`, `security/` |
| WB-6 never write a terminal run | B | `activities/_guard.py` | `unit/test_guard_and_activities.py`, `workflow/test_generation_flow.py::cancel_during_stage2` |
| WB-7 engine has no temporalio | B | | `::wd4` |
| WB-8 writes idempotent | P (skip logic here; the writes are the engine's) | `activities/generation.py::write_section` | `::idempotent...` against the fake only |
| WB-9 every engine write as `rag_activity` on a real DB | N | needs Postgres | |

### WQ queues (§5.3): 6 B, 2 P
| ID | St | Where | Test |
|---|---|---|---|
| WQ-1, WQ-2 tier rules, strict never shared | B | `queues.py` | `unit/test_queues.py` |
| WQ-3 one selector everywhere | P | selector here; callers (start, restart, resume) are the API's | |
| WQ-4 children on the parent's queue | B | `workflows/generation_workflow.py::_fan_out` | `workflow/` |
| WQ-5 six queues | B | `queues.py` | `test_queues.py` |
| WQ-6 plan read once from input | B | `queues.py::tier_for` | `test_queues.py` |
| WQ-7 refuse missing or unknown plan | P (hook only; refusal owed with IA-4) | `queues.py::PlanValidator` | `test_queues.py` |
| WQ-8 one strict queue per kind | B | `queues.py` | `test_queues.py` |

### WDT determinism (§5.4): 5 B
`tests`: `unit/test_architecture.py::workflows_use_no_clock...`, `replay/test_replay.py` (including a deliberate
non-determinism that must fail). Code: `workflows/*` (workflow time only, every loop waits).

### WV versioning (§5.5): 4 B, 2 P
| ID | St | Where | Test |
|---|---|---|---|
| WV-1 patch markers | P (infrastructure only; no change has shipped, so none exist) | `versioning/patches.py` | |
| WV-2 names never change | B | `llm_models.workers` constants, `@workflow.defn(name=)` | |
| WV-3 replay gate in CI | B (nightly live-history job is a stub) | `versioning/replay.py`, `.github/workflows/ci.yml` | `replay/test_replay.py` |
| WV-4 patch retention | B | `versioning/patches.py::retention_days` | `unit/test_patches.py` |
| WV-5 one Worker Deployment per queue | P (configured; not run against a versioning server) | `versioning/deployment.py`, `worker.py` | `unit/test_patches.py` |
| WV-6 pinned vs auto-upgrade | B | `@workflow.defn(versioning_behavior=)` | `unit/test_patches.py` |

### WA activities and graph (§5.6, §7): 16 B, 2 P, 1 N
| ID | St | Where / test |
|---|---|---|
| WA-1 scope first | B: `_guard.run_activity` opens `run_scope`; on real Postgres as a non-superuser, no tenant bound returns zero rows and another tenant's section is `RunGone` | `real_engine/test_adapter_on_the_real_engine.py::test_6_...` (`make real-engine-test`, Docker) |
| WA-2 re-resolve before corpus reads | B: `section_steps.py`, `generation.py`; `unit/test_guard_and_activities.py` |
| WA-3, WA-14, WA-16 background heartbeat, ids only | B: `activities/heartbeat.py`; `::heartbeats_beat...` |
| WA-4 ids only in results | B: `types.py`; `::write_section_returns_identifiers_only` |
| WA-5 repeat safely | B: `::idempotent`, `workflow/::restart...` |
| WA-6 translate errors once | B: `_guard.py`; `::errors_become_fixed...` |
| WA-7 write only through the engine | B: `test_architecture` |
| WA-8 never write a terminal run | B: `::never_writes_a_terminal_run` |
| WA-9 one graph, one activity | B: `graphs/section_graph.py` |
| WA-10 a death re-runs the activity | B: no resumable checkpoint exists |
| WA-11 checkpoint holds no text | B: `unit/test_graph.py` |
| WA-12 retry ceiling of two | B: `test_graph.py`, `workflow/::weak_verification...` |
| WA-13 sweep checkpoints | N: engine/API sweep |
| WA-15 clean rollback on cancel | B: `CancelledError` passes through `_guard` (the rollback is the engine's) |
| WA-17 quota is a result everywhere | B: `::quota_is_a_result_in_every_model_calling_activity`, `workflow/` quota tests |
| WA-18 no LangGraph checkpointer | B: `test_graph.py`, `test_architecture` |
| WA-19 correlation `run:section:attempt` | P: ref and attempt passed to every Gateway call; `retried_tokens` counted; joining Gateway usage is not built |

### WG gate and signals (§8): 8 B
All in `workflows/generation_workflow.py` (`_gate`, signal handlers) and `workflows/_common.py`; tests in
`workflow/test_generation_flow.py`: approval, revision, expiry, stale and duplicate signals (WG-1, 2), duplicate
cancel (WG-3, in `cancel_during_stage2`), lost cancel (WG-4), lost approve and revise (WG-7), restart with gate
re-armed from the row (WG-8), resume ignored when not suspended (WG-6). WG-5 (buffered signals) holds because
handlers only record; no dedicated test.

### WW waits, bounds, policies (§6, §8.5): 4 B, 1 P
| ID | St | Note |
|---|---|---|
| WW-1 one reconcile loop | B | `_common._wait_suspended`, `_gate` |
| WW-2 timer loop, continue-as-new only when suggested | P | timer loop built. `TenantSlots` continues as new (no test forces it). `GenerationWorkflow` does **not** continue as new and does not carry buffered signals |
| WW-3 run deadline | B | `workflow/test_failure_paths.py` (not durable across restarts, as the LLD says) |
| WW-4 poison breaker | B | `::poison_breaker_stops_the_run` |
| WW-5 explicit policies | B | `generation_workflow._fan_out`, `section_workflow`; cancel test |

### WR residency (§9.2): 2 B, 1 P, 1 N
WR-1 B (`::residency_refusal_is_terminal`). WR-2 P (strict pool only in `deploy/helm` values; no regions). WR-3 B.
WR-4 N (the LLD marks it not built).

### WT transactions (§10.2): 5 E. WEV events (§11): 6 E
The engine owns every transaction and every event. Here only exercised through the fake (for example WEV-5, one
terminal event, in `::fail_run_is_guarded...`). Verifying them needs the real database.

### WS security (§14): 9 B, 3 P, 1 E
| ID | St | Where / test |
|---|---|---|
| WS-1 tenant bound on every query | P: engine; the real-DB test covers the section read and the run writes only (`real_engine/`), not every query |
| WS-2 no roles or departments in input | B: `types.py`, plain types |
| WS-3 re-resolve per section | B: `section_steps.py`, `workflow/` scope tests |
| WS-4 tenant-prefixed keys | E |
| WS-5 strict only on strict queues | P: queue rules built; region and pod placement are deploy-side |
| WS-6 no content in history | B: `security/test_no_content_in_history.py` |
| WS-7 no content in logs, labels, spans | B: label allow-list; `caplog` check |
| WS-8 checkpoint holds no text | B: `test_graph.py` |
| WS-9 canary test | B: `security/` |
| WS-10 instruction not in history | B: scrubbed in `_common._begin`; `security/` |
| WS-11 failure hygiene | B: `_guard.safe_error`; `unit/test_guard_and_activities.py` |
| WS-12 failure converter | B: `codec/failure.py`; `unit/test_codec.py` |
| WS-13 payload codec | P: `codec/`; encrypted end to end (`integration/test_codec_end_to_end.py`); the codec server for the UI is not built; the key is a mounted file, not KMS (ADR-0002) |

### WO operator (§15.7): 2 P, 2 N
WO-1, WO-2 P (`operator/commands.py`: the listing needs registered search attributes, PL-8; the drain only reports
pollers, it does not stop them). WO-3 N (decided not built, WOQ-16). WO-4 N (it is the API's W-8 route).

## Sections without rule ids

| § | Topic | Status |
|---|---|---|
| 5.1, 14.3 | mTLS client cert from mounted files, system trust | B (`worker.py::_tls`); issuer and namespace names are other teams' (PL-7, PL-9); IAM has no part (IA-1 closed) |
| 5.2 | names and ids | B |
| 5.6 | timeouts, heartbeats, retries | B (`tuning.py`, `workflows/_common.py`) |
| 6.1 to 6.3 | the three workflows, LW-5 | B for the workflow logic; engine 1.1.0 supplies the outline, facts and coherence inputs (ADR-0003). The pack keys are an assumed Tender contract |
| 6.4 | regeneration | B: workflow path built and tested; instruction refinement reads `load_section_text` (ADR-0003; a read failure is retryable and bounded) |
| 7.2 | the 15 activities | 15 built; 3 blocked on the engine |
| 9.1 | quota suspend and resume | B; the 30-day failure is recorded (engine 1.1.0 has `suspended -> failed`; `test_suspension_timeout_records_a_failure_from_suspended`) |
| 9.5 | RunGone ends quietly | B; freeze pause **N** (owed, slice 7) |
| 10.1 | DB roles, grants | N (platform; PL-1) |
| 12.1, 12.2 | fan-out bounds, TenantSlots | B (document and tenant bounds); the global bound is fleet-size config only |
| 12.3 | control queue, capacities | B |
| 12.4 | load shedding by tier | N (no controller; `load_shed_steps` is not built) |
| 13.2 | terminal write retried 60 min, then `UnrecordedFailure` | B |
| 13.3 | recovery (W-8, orphan restart, zombie sweep) | restart-from-row B; the sweep is the API's (N) |
| 13.5 | graceful shutdown | B (`worker.py`, `integration/test_worker_bootstrap.py`) |
| 15.1 | 25 metrics | **20 built and emitted**: 11 on fastapicommon (activity side), 9 on Temporal's meter (workflow side). **5 not built, with the reason** (none is declared in code): `runs_suspended`, `runs_awaiting_gate`, `tenants_waiting` (state gauges, need a sweep), `load_shed_steps` (no controller), `cost_per_document` (no price source). `tokens_per_section` became the counter `section_tokens_total` and `workflow_history_events` the counter `workflow_history_events_total` (a histogram must end `_seconds`); `outcome` became `result`. **Observability Standard applied (rev 0.7):** every metric is Tier 3 `llm_workers_*` with `domain`, `service`, `environment` set centrally on both endpoints, and the platform linter passes on the real scrape of both (`make metrics-lint`), except the SDK's `temporal_*` families, a named exception (PL-12). All 20 built metrics are seen by the linter in the real run (`make metrics-lint` drives each path). Two scrape endpoints per pod (8080, 9464); the chart's PodMonitor for both is off by default (ADR-0006) |
| 15.2 | tracing | B: `init_tracing` (fastapicommon) where an exporter endpoint is set, plus the SDK tracing interceptor; tested in process only |
| 15.3 | logs | B: logs and traces go through fastapicommon (`get_logger`, `configure_logging`, `init_tracing`). One exception: the Temporal Rust core's own logs keep their own format |
| 15.4 | alerts | P (`alerts.enabled` renders a PrometheusRule with 5 of the 17 alerts: unrecorded failure, run deadline, poison breaker, lost signals, restart loop. The others belong to the cluster operator or the API, or need a gauge or a unit not checked; the reasons are in `docs/runbooks/README.md`. Never applied) |
| 15.5 | SLOs | N |
| 15.6 | 16 runbooks | B as documents (`docs/runbooks/`, 16 plus an index; written from the code and the LLD, never exercised in an incident) |
| 16 | configuration | B (`config.py`), except workflow tunables are code (ADR-0004) |
| 17.1 to 17.3 | image, Helm, scaling, placement | B (the chart was rendered and linted with `helm` v4.3.2 on 2026-11-27: `helm lint` and `helm template` for dev, staging and prod values, 20 objects each, no error; never applied to a cluster, no schema validation. autoscaling is off by default and the chart refuses to render it on without `kedaTemporalScaler`; per-environment `values-dev|staging|prod.yaml` exist; `make chart-check` also validates the manifests with kubeconform (the KEDA and Prometheus Operator CRDs are skipped); the NetworkPolicy is allow-all until `networkPolicy.egress` is set (PL-3). Image not built; network policy egress open until PL-3) |
| 17.4, 17.5 | rollout, rollback | P: config only, no runbook |
| 17.6 | DR drill | N |
| 17.7 | pipeline | P: `ci.yml` written, never run |
| 18.1 | unit, workflow, replay, architecture, contract | B; 96% coverage |
| 18.1 | integration on real Postgres | **P**: `make real-engine-test` runs the workers' adapter and activities against the real engine 1.1.0 on throwaway Postgres 18, an S3 emulator (floci) and Valkey (Docker, not in `make ci`); Tender is a stub; no Temporal. End to end through Temporal on real Postgres and load: N |
| 18.7 | failure injection | P: the cases that fit the fake (quota per phase, Gateway outage, residency, scope, purge, cancel in stage 2, lost signals, breaker); kill-a-worker, DB read-only, S3 and Temporal restarts are N |
| 18.8 | security tests | P: canary, codec; two-tenant isolation B for the section read on real Postgres (`real_engine/test_6_...`), N elsewhere |
| 19 | slices 1 to 6 | logic built; slices 7 and 8 partial |

## Gaps, in the order I would close them
1. Engine gap 6 (ADR-0003: the engine's metrics identity, engine 1.2.0, not merged): until then the engine's own metrics are off in a worker. Not yet run: the workers' adapter against a real database.
2. A real-Postgres integration suite (WB-9, WA-1, WS-1, WT, WEV).
3. `GenerationWorkflow` continue-as-new with carried signals (WW-2).
4. Emit the 5 missing metrics and write the alert rules, SLOs and runbooks (§15.4 to §15.6).
5. Slice 7 items: freeze pause, zombie sweep (the API's), load shedding.
