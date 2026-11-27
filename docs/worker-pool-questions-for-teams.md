# llm-workers: questions for other teams (v3)

From the TL's review of the llm-workers LLD v0.1 and the workers LLD's own open questions. Updated 2026-11-23 after the Workflow reply `resp-llm-workers-wf` (22 Nov, from Workflow Engine and Tender).

**Workers LLD draft 2 is written on the defaults in Part A.** A different answer reopens only the sections its row names. Part B rows are our **summary** of each team's reply, not quotations: the source is `resp-llm-workers-wf` itself. A row moves to Part B only when its team has answered it.

Reference: workers LLD `docs/lld/llm-lld-workers-service.md` (§ numbers and WOQ ids), the review (P0, WF, G ids), `docs/worker-pool-contract.md`.

**Count:** 18 open, 28 closed, 46 in total. (Updated 2026-11-25 after the Workflow Stream Gateway reply: PL-10 and the LLM-1, 2, 4, 5, 7, 8 answers are closed; PL-11, TE-2 and I-2 are open. The TL's rows H-1 to H-9 were decided the same day and are closed.)

## What changed since v2

- The Workflow team answered **WE-3, WE-4, WE-5 and WE-8**. They are closed (Part B).
- Their answers move several rows to the platform / cluster operator team: certificates, retention and search attributes, server version, namespace names.
- **IA-2 and IA-5 are removed from IAM.** The Workflow team says IAM does not decide retention or search attributes (now PL-8) or the event consumer (now PL-10). **IA-1 is kept as a confirm row**, because Workflow's earlier note said "ensured by IAM (envoy)" and their newer reply says the mesh does not carry the Temporal connection; only IAM could settle it. **IAM answered on 26 November 2026: no IAM or Envoy part; IA-1 is closed (Part B).** The certificate itself is PL-7, still open.
- New rows: PL-6 to PL-10, C-1 (Commercial), H-9 (TL), TE-1 (Tender).
- **2026-11-25, Workflow Stream Gateway reply.** The LLM subsystem runs its own event consumer (LLM-8), so **PL-10 is closed**. New open rows: **PL-11** (the consumer's namespace, Platform), **TE-2** (Tender confirms it no longer relays progress), **I-2** (Ingestion's browser events). LLM-1, 2, 4, 5, 7 and 8 are in Part B.
- **2026-11-25, the TL's rows.** H-1 to H-9 were decided (the TL left them to the engineer) and moved to Part B. Two builds are owed: the freeze pause (H-4) and refusing an unknown plan (H-9).

---

## Part A. Open questions, by team

### 1. Platform (cluster operator)

The team that runs the Temporal cluster and the database and cloud roles.

| # | Question | Why we ask | Our default | Blocks | Status |
|---|---|---|---|---|---|
| PL-1 | `rag_activity` is `NOLOGIN` by design. Which **login role** (a member of `rag_activity`) will the workers connect as, and who provisions it (OQ-67)? One per tier (standard, enterprise, strict) so a strict pool's credentials cannot reach standard data? | A worker needs a login; today only a group role exists | `llm_workers_login`, one per tier | Slice 1 | waiting |
| PL-2 | The IRSA role for the workers: `s3:PutObject` on `tenants/*` and `kms:Encrypt` with the `tenant_id` encryption context, like `deploy/irsa/policy.json` | Section text is written to S3 by the worker | Same policy as the API's | Slice 1 | waiting |
| PL-3 | Network policy only: will the `llm-workers` namespace be admitted to Tender, the Gateway and `llm-context` (WOQ-8)? The identity itself is IA-3 | Workers re-read memberships and call the Gateway per section | The platform's service identity | Slice 1 | waiting |
| PL-4 | Record the `llm-workers` namespace in the platform topology (the Definition Service limits connector aliases to `.tender-app.svc.cluster.local`, WF-6) | No impact while workers expose no API | Nothing | Not blocking | waiting |
| PL-5 | Outbox relay: worker events (`OutlineReady`, `SectionCompleted`, ...) stop when the API pods are down, because the relay runs only in the API pods. Acceptable, or should workers run a relay? | Events wait in the outbox | Accept, and alert on outbox age | Slice 7 | waiting |
| PL-6 | **Server version (from WE-5).** Which Temporal server version do you run, and does it support Worker Deployments? Do the defaults hold on our namespace (1000 task queues per deployment, 100 deployments per namespace)? | The workers pin versions with a deployment name plus build ID. Sections and extraction are pinned; the parent run, which can wait 60 days, auto-upgrades with replay-checked patch markers so old builds need not stay alive that long | Worker Deployments: sections pinned (your default), the long parent auto-upgrades (workers LLD LW-12) | Slice 1 | waiting |
| PL-7 | **Certificates (from WE-3).** Workflow dials Temporal with a client cert and key from mounted files (`TEMPORAL_TLS_CERT_PATH`, `TEMPORAL_TLS_KEY_PATH`), trusting the system CA store. Who issues ours? One certificate for the API and one for the workers (so the namespace can tell them apart), or one shared? | Workers cannot connect without it | Two certificates, same setting names, no CA path | Slice 1 | waiting |
| PL-8 | **Retention and access (from WE-4).** What history retention will you set on the `llm-generation` namespace? Will you register search attributes `RunKind`, `ArtefactId`, `RunStatus` (`TenantId` exists) and let only the API's certificate start and signal? | An operator needs a tenant-scoped list of live runs; history holds identifiers only and must expire | 14 days after close, those four attributes, API-only start and signal | Slice 1 | waiting |
| PL-9 | **Namespace names (from WE-2).** One namespace per deployment is agreed. What are the exact names for staging and prod? | They go into `deploy/helm/values-staging.yaml` and `values-prod.yaml` | `llm-generation-<env>` | Slice 1 (small config PR) | waiting |
| PL-11 | **Namespace (from LLM-3).** The LLM subsystem's own event consumer calls the Workflow Stream Gateway as ServiceAccount `llm-retrieval-generation`. What namespace does it run in, for the gateway's mesh policy? | The gateway keeps a placeholder namespace and refuses our calls with `403` until it is set | Nothing; calls stay refused | Forwarder go-live | waiting |
| PL-12 | **Registry: `temporal_` prefix (Observability Standard).** The Temporal SDK's own telemetry (21 families on the runtime's endpoint, `temporal_*`) is reported by `fastapicommon-metricslint` as an unclassified name, because `exempt_prefixes` is `go_`, `process_`, `promhttp_`, `python_`. Please add `temporal_` | The names are the SDK's; renaming or hiding them is not ours to do | Named exception in ADR-0006; the test pins it | Linter on the SDK endpoint | waiting |
| PL-13 | **Registry: a family with no series.** The linter reads the `service` label from a series, so a labelled collector that has not recorded yet is reported as `llm__<metric>`. Should it fall back to the registry identity or skip the family? | Our CI drives every path once to avoid it | Drive each path in the test | None | waiting |
| PL-14 | **Registry: label vocabularies on Tier 3.** A registry label's value list applies to any metric using that name (`outcome` accepts only `success`, `failure`, `error`). Is that intended for service-owned metrics? | We renamed our label to `result` | `result` | None | waiting |
| PL-15 | **Registry: two `llm_*` candidates.** `llm_scope_revoked_total` and `llm_tokens_total`, shared with `llm-retrieval-generation` (`docs/observability-registry-requests.md`) | Domain-shared names need ratification | Keep `llm_workers_*` names | None | waiting |

### 2. IAM team

| # | Question | Why we ask | Our default | Blocks | Status |
|---|---|---|---|---|---|
| IA-3 | **How the workers authenticate to IAM (WOQ-8).** A worker has no user request. Each section re-reads the principal's memberships (`GET /users/:id/memberships`), calls the Tender ACL (TAC-4) and `llm-context`. This row owns the identity itself (PL-3 covers only network policy). What service identity do the workers use, which headers do they send (the gateway is not in front of them), and will IAM admit the `llm-workers` namespace? Is the tenant status header available outside a request? | A worker that cannot re-resolve scope must fail closed, and one with no way to read IAM cannot run | The platform's service identity, the same calls as the API makes | Slice 1 | waiting |
| IA-4 | **`x-plan` values.** We route to the enterprise queues when the plan, lower-cased, is `enterprise` (`ENTERPRISE_PLANS`). Does `x-plan` carry the plan **name** (`starter`, `pro`, `enterprise`) or the `model_tier` value (`basic`, `standard`, `premium`)? If it carries `premium`, our check never matches and nothing errors. What are the exact strings? If a tenant changes plan during a run of up to 60 days, which plan applies? | The queue is chosen once at start | The plan at start, for the life of the run | Slice 0 | waiting |

### 3. Commercial

| # | Question | Why we ask | Our default | Blocks | Status |
|---|---|---|---|---|---|
| C-1 | The Workflow team chooses a dedicated queue from the Entitlements `dedicated_workflow_queue` grant and the plan in `TenantStateChanged`, and says admitting the LLM namespace to the Entitlements read is a Commercial item. Will you admit it, and is the plan the field we should read? | Queue choice must agree with what the tenant bought | Plan from the identity (`x-plan`) only | Slice 0 | waiting |

### 4. TL

All nine rows (H-1 to H-9) were decided on 25 November 2026 and moved to Part B.

### 5. Tender

| # | Question | Why we ask | Our default | Blocks | Status |
|---|---|---|---|---|---|
| TE-2 | **Progress path (from LLM-8).** Please confirm that Tender no longer relays progress to the browser: it reaches the browser through the Workflow Stream Gateway, fed by the LLM subsystem's own consumer. Tender's HLD changes on its side | Our HLD and LLD stop saying "Tender relays progress" only when Tender agrees | Progress through the gateway only | Forwarder go-live | waiting |
| TE-1 | Your reply is provisional until HLD v0.10 is signed off. Two points need Tender's own agreement: what the closing step does with `sections_failed` and `sections_gap` (T-2), and what a regeneration request carries when an instruction contradicts an established fact (T-3, your TND-Q37) | Tender must not close a partial document | Tender reads the two counts; the conflict is surfaced to the caller | Slice 2 and 6 | waiting |
| TE-3 | **The pack keys the workers read (engine 1.1.0).** `DomainProfile.outline_skeletons` (`{capability: [{ref, section_type, is_foundation}]}`) and `DomainProfile.fact_keys` (a list of strings) are read from the pinned pack's JSON by the engine. The names and shape are the workers repository's proposal. Please confirm them for Tender's Domain Profile, or give the names Tender uses | The engine reads absent keys as empty, so a pack that lacks them loads but produces no outline (`PackOutlineMissing`); a wrong name would look the same | The names above | Slices 2 and 6 | waiting |

### 6. Ingestion author

| # | Question | Why we ask | Our default | Blocks | Status |
|---|---|---|---|---|---|
| I-2 | **Browser events (from LLM-6).** Does Ingestion publish events for a browser? If so: its event `source`, its types, and whether they carry `recipients` | The Workflow Stream Gateway routes no Ingestion events until you say | None | Not blocking | waiting |
| I-1 | **WOQ-12 / D-4:** will the Ingestion workers live in the same repository (`llm-workers`) as a second workspace member? | The HLD puts both in one repository as separate deployments | Workspace layout; specify only generation | Not blocking | waiting |

---

## Part B. Closed (answered)

Our summary of the Workflow and Tender rows, from `resp-llm-workers-wf` (22 Nov 2026, version 1.0), unless marked earlier.

| # | Their answer | Our resolution | Lands in |
|---|---|---|---|
| WE-1 | Yes. One cluster serves all domains with one namespace per domain... `llm-generation` is recorded as a namespace of the cluster. Shard count, capacity and upgrade notice are cluster-operator items | **Closed.** | Workers LLD §5.1; contract §1 |
| WE-2 | One namespace per deployment environment... The exact pattern is the cluster operator's to set. | **Closed.** Names are PL-9. | Helm values (PL-9); LD-107 |
| WE-3 | The Execution Service dials with a client certificate and key from mounted files (`TEMPORAL_TLS_CERT_PATH`, `TEMPORAL_TLS_KEY_PATH`) and trusts the system store; no CA path, no API key; API and worker share one certificate. The mesh... does not carry this connection. Issuing certificates is a cluster-operator action | **Closed.** Our names match. Envoy is not involved, so the earlier ensured by IAM (envoy) is withdrawn. Remove our optional `TEMPORAL_TLS_CA_PATH`. Issuer is PL-7. | `config.py`, `.env-example`, contract §1, LD-107 (small config PR); workers LLD §14.3 |
| IA-1 | **IAM, 26 November 2026.** IAM and Envoy have no part in the worker-to-Temporal connection. Envoy Gateway covers inbound JWT validation and `ext_authz` on mesh-internal HTTP calls; the Temporal connection is a direct outbound TLS channel. IAM does not issue, rotate or validate the certificate (a cluster-operator action, PL-7), and supplies no CA path. The earlier "ensured by IAM (envoy)" note is withdrawn | **Closed.** | Workers LLD §14.3, rev 0.6 |
| WE-4 | Retention and search attributes are per-namespace settings set by the cluster operator; no retention value is recorded; `TenantId` exists; start/signal limits are an operator rule | **Closed.** Values are PL-8. | Workers LLD §5.1, §14 |
| WE-5 | The llm-workers pin the versions: Worker Versioning on, a deployment name plus a build ID, default behaviour pinned. The cluster operator states the server version | **Closed.** Our default moves from build-id sets to Worker Deployments (sections pinned; the parent auto-upgrades, workers LLD LW-12). Server version is PL-6. | Workers LLD §5.5, §17.4 |
| WE-6 | No request quota is set by Workflow; it is a cluster setting and depends on callers | **Closed with our default** (`FANOUT_MAX_GLOBAL`). Reopen if a limit is set. | Workers LLD §12 |
| WE-7 | Removed. It is a workflow-side pre-warm, dropped from the next revisions; no LLM consumer expected | **Closed, nothing to do.** Our LLM pre-warm wording was wrong. | Workers LLD (drop the WF-7 note) |
| WE-8 | Event consumer: configuration, matching by event type, onboarding agreed when types are ready. Stream Gateway: fixed event set today, planned to become configurable, no date. Consumer choice is a Platform decision | **Closed.** Superseded by LLM-8: we run our own consumer, so there is no platform choice (PL-10 closed). | Workers LLD §15.5 |
| PL-10 | (our row) Which event consumer does the platform standardise on for LLM events | **Closed 2026-11-25** by LLM-8: our own consumer. No Platform decision needed; only the namespace (PL-11) | Workers LLD §15.5 |
| LLM-1 | Event `source` is `llm-retrieval-generation`, also for the workers' events | **Closed.** | LLD rev 2.144; workers LLD §11 |
| LLM-2 | The `recipients` shape is final (LD-83) | **Closed.** | Workers LLD §11, WEV-1 |
| LLM-4 | Tender: administrators do not receive LLM events; Tender names the users and departments in `recipients` | **Closed.** | Nothing here |
| LLM-5 | Tender: all ten event types reach the browser as `llm.<type>` | **Closed.** | Workers LLD §11 |
| LLM-7 | Agreed: a message that can never succeed goes to the dead-letter queue, a transient failure retries | **Closed.** | Workers LLD §15.5 |
| LLM-8 | Decided: our own consumer posts each event unchanged to `POST /api/v1/internal/events` as the system caller; the Workflow Engine's consumer does not carry LLM events | **Closed from our side.** Tender's confirmation is TE-2. LLM-3 (namespace) is PL-11, LLM-6 is I-2 | Workers LLD §15.5 |
| WE-9 | The difference is acceptable. Dedicated queue comes from the `dedicated_workflow_queue` grant and the plan in `TenantStateChanged`; tier read at run start; unknown or missing plan refuses the run. Entitlements admission is a Commercial item | **Closed.** Plan strings are IA-4, unknown plan is H-9, Commercial is C-1. | Workers LLD §5.3 (drop the per-tenant-queue paragraph) |
| T-1 | Skeleton only (Tender HLD §7.5, TND-D21, §12.6) | **Closed.** Provisional until HLD v0.10 sign-off. | Workers LLD LW-9; slice 2 |
| T-2 | Handled on the LLM side; Tender decides how to proceed | **Closed for us.** `GenerationCompleted` carries both counts. Tender's closing step is TE-1. | LD-108; workers LLD WOQ-4 |
| T-3 | Goes to the caller (TND-Q37) | **Closed, our reading:** keep the established value, surface the conflict. Detail is TE-1. | LLD A-5 and A-7; workers LLD WOQ-5 |
| H-1 | Decided 25 Nov 2026 (the TL left it to the engineer): yes, `llm-workers` owns the section graph and orchestration activities | **Closed.** D-2 stays recorded until the HLD owner updates the repository table | Workers LLD §4.1, D-2 |
| H-2 | Six queues | **Closed.** | Workers LLD §5.3 |
| H-3 | One strict queue per kind until regions are named. A long run stays on its start queue, so a region added later keeps the old strict workers until those runs end | **Closed.** Reopens when regions are named | Workers LLD §5.3, §9.2, WQ-8 |
| H-4 | **A freeze pauses long runs** until reactivation (`tenant_suspended`), like quota. A purge still ends runs | **Closed. Build owed, slice 7** (WOQ-18) | Workers LLD §9.5 |
| H-5 | As built. Tender sees the completeness verdict on `generation_completed` and `artefact_regenerated` | **Closed.** | Workers LLD §6.1, D-9 |
| H-6 | One wheel | **Closed.** | Workers LLD §4.6 |
| H-7 | Re-based on HLD v0.30, then v0.31 (LLD rev 0.5; its only change is the progress relay already in D-19). The v0.29 to v0.30 diff changes only the §18.2 repository name | **Closed.** | Workers LLD header, D-7 |
| H-8 | Engine by git tag; starting SLO values | **Closed.** Calibrate at slice 8 | Workers LLD §4.6, §15.5 |
| H-9 | **A missing or unknown plan is refused**, like the Execution Service | **Closed. Build owed** (WOQ-19), lands with IA-4 | Workers LLD §5.3, WQ-7 |

How changes ship: documentation and the workers LLD need no new code PR (they go into #20 if it is still open, otherwise a small docs PR). Configuration changes (namespace names; `TEMPORAL_TLS_CA_PATH` is already removed) are a small code PR with a CHANGELOG entry. Code in `llm-workers` waits for that repository to exist.

## Part C. What the open rows will change when they close

| Row | If answered as our default | This repo | Workers LLD |
|---|---|---|---|
| PL-1 to PL-5 | Login per tier, same IRSA policy, relay stays in API pods | `deploy/` only if a policy file is added | §10.1, §16, §17 |
| PL-6 | Worker Deployments: sections pinned, the parent auto-upgrades; build-id sets if unsupported | None | §5.5, §17.4 |
| PL-7 | Two certificates, no CA path | None: `TEMPORAL_TLS_CA_PATH` is already removed | §14.3 |
| PL-8 | 14 days, four attributes, API-only start | None | §5.1, §14 |
| PL-9 | `llm-generation-<env>` | `values-staging.yaml`, `values-prod.yaml` | §5.1, §16 |
| PL-11 | Placeholder stays; calls refused with `403` until set | None until the forwarder is built | §15.5 |
| PL-12 to PL-15 | Registry answers (SDK prefix, empty families, label vocabulary, shared names) | None until answered; the exception is pinned in a test | §15.1 |
| TE-2 / I-2 | Progress through the gateway only; no Ingestion events | None | §15.5 |
| IA-3 / IA-4 | Service identity; plan at start | None until strings are confirmed | §14, §9.2 |
| C-1 | Plan from identity only | None | §5.3 |
| H-1 to H-9 | Decided (Part B) | `rag-engine` stays one wheel; a freeze pauses runs (build owed, slice 7); `choose_queue` refuses an unknown plan (build owed, with IA-4) | §1.2, §20 |
| TE-1 | Tender reads the counts | None | WOQ-4, WOQ-5 |
| TE-3 | The keys stay as proposed | A pack with other names yields no outline until the pack or the engine reader changes | §6.1, ADR-0003 |
| I-1 | Workspace layout only | None | §3 |

**Tender's items in one file, in plain language (shareable with Tender):** `docs/tender-gaps.md`.
