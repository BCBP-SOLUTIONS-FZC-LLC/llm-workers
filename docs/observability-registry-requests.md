# Requests to the platform observability registry (draft, not adopted)

Status: **draft for the platform team**. Nothing here is adopted and no metric name in the code has changed:
the workers still emit their own Tier 3 `llm_workers_*` names. A shared name is used only after the registry
owners ratify it. Source for the sibling side (read-only): `llm-retrieval-generation`,
`src/rag/infrastructure/observability/workflow_metrics.py` and `generation_metrics.py`, whose metrics are all
`llm_retrieval_generation_*` (Tier 3). The decision tree puts a metric with the same meaning in two services of
one domain in `<domain>_*`; here the domain is `llm`, which the registry lists as a domain but has no metric in.

**Where the two services overlap.** Of the sibling's 17 counters, 5 histograms and 1 gauge in those two files, only two share a
meaning with a worker metric. The workers' `gate_expired_total` and `quota_suspensions_total` have **no
counterpart** in either file: the sibling counts gate and resume actions it *accepted*
(`gate_action_total`), which is a different event from a gate *expiring*, and it has no suspension metric.
They stay Tier 3.

Not candidates: `workflow_start_total` (the sibling counts start requests by outcome at the API; the workers
`workflow_started_total` counts executions that began: different events), `platform_dependency_request_seconds`
(workers call dependencies through the engine), and every other metric of either service.

## Request 1: `llm_scope_revoked_total`

| Field | Proposal |
|---|---|
| Tier | domain (`llm_*`) |
| Type | counter |
| Semantic definition | Runs that ended because re-resolving the run's scope found no collection the caller may read (`scope_revoked`), counted once when the run is ended. Identical in every service of the domain |
| Required labels | `service`, `environment` (and the constant `domain`); no others |
| Allowed values | none beyond the constant labels |
| Aggregation | `sum by (environment) (rate(llm_scope_revoked_total[5m]))`; per `service` to see which side ended the run; never per tenant (no tenant label) |
| Emitting services | `llm-retrieval-generation` (today `llm_retrieval_generation_scope_revoked_async_total`, no labels) and `llm-workers` (today `llm_workers_scope_revoked_total`, no labels) |

## Request 2: `llm_tokens_total`

| Field | Proposal |
|---|---|
| Tier | domain (`llm_*`), **needs the owners' decision** (the label sets differ today) |
| Type | counter |
| Semantic definition | Tokens (prompt plus completion) the Model Gateway reported for model calls made by the service, summed over every attempt |
| Required labels | `service`, `environment` (and the constant `domain`) |
| Proposed extra labels | `task_class` (a bounded enum of model task classes) and `capability` (the pack's capability). The sibling also has `model_profile`, and the workers have `stage` and `tier`: those stay service-specific, so the question is whether they become approved labels of the shared metric or the two services keep their own names |
| Allowed values | `task_class`: the Gateway's task classes; `capability`: the pack capabilities (bounded by the packs). To be defined by the registry owners |
| Aggregation | `sum by (environment, capability) (rate(llm_tokens_total[5m]))`; a rate of tokens is a cost proxy, never priced here |
| Emitting services | `llm-retrieval-generation` (today `llm_retrieval_generation_tokens_total{task_class,model_profile,capability}`) and `llm-workers` (today `llm_workers_section_tokens_total{stage,tier}`) |

The workers' `retried_tokens_total` (tokens of an attempt that was repeated) is unique to the workers and stays
Tier 3.

## Other questions for the registry owners (also in `docs/worker-pool-questions-for-teams.md`)

* **PL-12:** add `temporal_` to `exempt_prefixes` (the Temporal SDK's own telemetry; 21 families, none can follow
  the `_total` and `_seconds` rules).
* **PL-13:** the linter cannot classify a labelled collector that has no series yet (the service label is read
  from the series, so the prefix is computed as `llm__`). Should `check` fall back to the registry identity,
  or skip a family with no samples?
* **PL-14:** a registry label name's value vocabulary is applied even to Tier 3 metrics (`outcome` only accepts
  `success`, `failure`, `error`). Is that intended? The workers renamed their label to `result`.
