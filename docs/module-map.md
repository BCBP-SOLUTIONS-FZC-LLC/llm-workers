# Module map

For every module: purpose, assumptions, LLD sections, replay-safety, security. `generation/src/llm_workers_generation/`.

| Module | Purpose | Assumptions | LLD | Replay safety | Security |
|---|---|---|---|---|---|
| `tuning.py` | Workflow-affecting constants | Values are the LLD's starting values | §5.6, §12.1, §16 | Pure constants; a change that alters commands needs a patch (ADR-0004) | none |
| `errors.py` | Error codes, fixed messages, error to failure-reason map | Codes match §4.4 | §4.4 | Pure | The only text that can reach history from an activity failure (WS-11) |
| `types.py` | Activity requests and results beyond `llm_models.workers` | Dataclasses carry through the SDK's JSON converter | §4.3, §7.2 | Frozen data | No text field (a test scans every field name) |
| `queues.py` | Six queues, control queues, `task_queue_for` | `ENTERPRISE_PLANS = {enterprise}` (WQ-6); unknown plan goes standard; refusal is a hook (WQ-7) | §5.3 | Pure | Strict never lands on a shared queue (WQ-2) |
| `config.py` | Validated process settings | Queue and deployment name are derived, never configured | §16 | n/a | Secrets are `SecretStr` |
| `workflows/_common.py` | Policies, wait loop, failure, end handling | The row is the truth; a signal only wakes a wait | §5.6, §8.5, §9.1, §13.2 | Workflow time only; every loop waits | Passes ids; reads only the error *type* |
| `workflows/generation_workflow.py` | The document run, gate, fan-out, quota, breaker, deadline | `outline` revisions pass `(ref, type)` pairs; a lost revise falls back to the pending rows | §6.1, §6.4, §8, §9.1 | `asyncio.Semaphore`; handlers only record; auto-upgrade needs patches (WV-1) | Scrubs `instruction` from forwarded input (WS-10) |
| `workflows/section_workflow.py` | Acquire slot, write section, release in `finally` | Child id `{parent}:s:{section}:{stage}` | §6.2, WW-5 | Pinned | Section id and enums only |
| `workflows/extraction_workflow.py` | Extraction with quota suspension | A `refused` extraction is a complete run (the engine closed it) | §6.3 | Pinned | Identifiers only |
| `workflows/tenant_slots_workflow.py` | Per-tenant slots entity | See ADR-0005 | §12.2 | State in `@workflow.init`; continue-as-new after handlers | Request ids only; validator rejects bad input |
| `activities/ports.py`, `engine_api.py` | The engine port and its one adapter | ADR-0001, ADR-0003 | §4 | n/a | The only importer of `rag`; no content logged |
| `activities/_guard.py` | Scope first, error translation, gone/terminal/quota as results | Store errors recognised by exception module | §4.4, WA-1, WA-6 | n/a | Fixed message, no details, `from None` |
| `activities/generation.py` | The 13 run activities | Facts and coherence call the model with the `extract` task and parse JSON | §7.1, §7.2 | n/a | Ids only in results; parse failure never echoes the answer |
| `activities/section_steps.py` | The section's building blocks | Query is built from section type, ref and pack vocabulary; "deeper" doubles the rerank window | §7.3 | n/a | Content stays on the object, in memory |
| `activities/slots.py` | `acquire_slot`, `release_slot` | ADR-0005 | §12.2 | n/a | Ids only |
| `activities/heartbeat.py` | Background heartbeat | Interval is a third of the timeout | WA-14, WA-16 | n/a | Details are ids and counts |
| `graphs/section_graph.py` | LangGraph section state machine | Retry ceiling of two (ASV-2) | §7.3 | n/a | No checkpointer; state holds no text; trace holds names, ms, counts |
| `codec/` | AES-GCM codec, failure converter | ADR-0002 | §14.2 | Outside the sandbox | Encrypts every payload and failure attribute |
| `observability/metrics.py` | The 9 workflow-side metrics, on Temporal's meter | Named exception: workflow code cannot import fastapicommon (ADR-0006) | §15.1 | SDK meter, suppressed on replay | Label allow-list rejects tenant, user, document |
| `observability/app_metrics.py` | The 11 activity-side metrics, on `fastapicommon` (`new_counter`, `new_histogram`) | Needs `init_metrics` first; no-op when metrics are off; tokens are a counter because histograms must end `_seconds` | §15.1 | n/a | Same allow-list; never imported by workflows |
| `observability/setup.py`, `ops.py` | Logging, metrics identity, tracing, the Temporal runtime (SDK telemetry: a named exception), the ops server (`/health`, `/live`, `/ready`, `/metrics`) | Tracer provider from `fastapicommon.telemetry` and the interceptor only when an endpoint is set | §15.2, §17.1 | n/a | Spans carry ids and counts |
| `versioning/` | Deployment config, patch registry, replay | No patch has shipped | §5.5, §18.2 | The replay gate | none |
| `operator/commands.py` | WO-1, WO-2 | Needs search attributes (PL-8) | §15.7 | n/a | Query values restricted to `[A-Za-z0-9_:-]` |
| `worker.py` | Bootstrap: two workers per pod, grace shutdown | Logs through `fastapicommon` | §3.4, §13.5 | n/a | mTLS from mounted files; system trust store |
