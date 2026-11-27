# Runbooks

One short runbook per situation the design names (LLD section 15.6). Each says what you see, what to check and what to do. They describe this repository's behaviour; what other teams own (the Gateway, the database, the cluster, the API's routes) is theirs to act on.

Rules that hold in all of them: the run's row is the truth and a signal is only a hint; only the API service starts, restarts or cancels runs; nothing here edits rows by hand; nothing readable is in Temporal history, so do not look for text there.

| Runbook | When |
|---|---|
| [A stuck queue](stuck-queue.md) | Tasks wait on `llm-generation*` or `llm-extraction*` |
| [A non-determinism failure after a deploy](non-determinism-after-deploy.md) | Workflow tasks fail repeatedly after a new build went out |
| [An unrecorded failure](unrecorded-failure.md) | A workflow ended with the failure type `UnrecordedFailure` |
| [A quota suspension storm](quota-suspension-storm.md) | Many runs are `suspended` with reason `quota_exhausted` |
| [A Model Gateway outage](gateway-outage.md) | `write_section` and the model steps fail with `InferenceUnavailable` |
| [A database outage](database-outage.md) | `Internal` and `StoreUnwritable` activity failures rise |
| [A lost workflow (the run's row says running, no workflow exists)](lost-workflow.md) | A run is `generating` or `suspended`, but Temporal has no open workflow for it |
| [A quiet async run](quiet-async-run.md) | An asynchronous run shows no progress for longer than `QUIET_ASYNC_RUN_SECONDS` |
| [A lost signal](lost-signal.md) | `llm_workers_reconcile_recovered_total` rises: a wait found the answer in the row, not fro |
| [A worker rollback](worker-rollback.md) | A new worker build misbehaves |
| [A strict-residency capacity shortage](strict-residency-capacity.md) | Strict-tier runs fail or wait with `residency_capacity`: no capacity in the tenant's regio |
| [A purge during a run](purge-during-a-run.md) | A tender or tenant is purged while its run is live. |
| [A tenant freeze](tenant-freeze.md) | A tenant is frozen |
| [The poison-section breaker](poison-section-breaker.md) | `llm_workers_poison_breaker_total` rises: most ended sections of a document failed with th |
| [An expiring Temporal client certificate](expiring-temporal-certificate.md) | Fewer than 14 days to the expiry of the worker's client certificate. |
| [A payload-codec key rotation](payload-codec-key-rotation.md) | The key that encrypts what is written to Temporal history must be replaced. |

## Alerts: what is in the chart and what is not

`alerts.enabled=true` renders a `PrometheusRule` (needs the Prometheus Operator CRDs) with five alerts the workers' own metrics can express: unrecorded failure, run deadline, poison breaker, lost signals (a rising `reconcile_recovered_total`) and a restart loop (kube-state-metrics).

The rest of the LLD's alert list is **not** in the chart, for these reasons:

* **Queue backlog age, control-queue backlog, workflow task failures:** these are Temporal server metrics, owned by the cluster operator.
* **Schedule-to-start latency:** the SDK's `temporal_activity_schedule_to_start_latency` histogram has no unit in its name and the unit has not been checked, so no threshold is written.
* **Workflow history above 10,000 events:** `llm_workers_workflow_history_events_total` is a counter, not a gauge, so it cannot express a size.
* **Suspended runs over 20 for 24 hours, gate expiries above the weekly norm:** need a gauge or a baseline that does not exist yet.
* **Quiet async runs:** the API service's sweep.
* **Outbox age, database role errors:** database-side, the API's and the platform's.
* **Certificate expiry:** needs the certificate's expiry as a metric; none is exported here.
* **SLO burn:** the objectives are starting values and not calibrated.
