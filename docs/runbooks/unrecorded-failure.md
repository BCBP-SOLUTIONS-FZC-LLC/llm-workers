# An unrecorded failure

**What you see.** A workflow ended with the failure type `UnrecordedFailure` (alert: `llm_workers_unrecorded_failures_total`). The run could not write its own ending for the whole window (60 minutes), so the row is still open.

**Check and do.**
1. The cause is almost always the database (see `database-outage.md`): `fail_run` or `complete_run` could not write.
2. Read the run's row through the API: it is not terminal, and no `GenerationFailed` or `GenerationCompleted` event was written.
3. Once the database is healthy, restart the run with the API's restart route (W-8). It continues from the row.

**Do not.** Do not hand-edit the run's row or the outbox.
