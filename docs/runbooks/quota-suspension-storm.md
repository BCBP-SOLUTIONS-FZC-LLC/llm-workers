# A quota suspension storm

**What you see.** Many runs are `suspended` with reason `quota_exhausted`; `llm_workers_quota_suspensions_total` climbs; more than 20 runs stay suspended for over 24 hours.

**Check and do.**
1. A quota refusal is a result, not a failure: the run waits (up to 30 days from its `suspended_at`) for a `resume` signal or for the hourly row check.
2. Check the Model Gateway's quota for the tenant or the platform. Raising it is the Gateway's team's action, not ours.
3. When quota returns, the API (or an operator through it) resumes runs; a lost `resume` is recovered by the hourly read (`lost-signal.md`).
4. After 30 days a suspended run fails with `quota_exhausted` and that is recorded.

**Do not.** Do not cancel suspended runs to clear the alert: each one holds the user's progress.
