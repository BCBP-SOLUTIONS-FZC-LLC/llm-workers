# A lost signal

**What you see.** `llm_workers_reconcile_recovered_total` rises: a wait found the answer in the row, not from a signal (an approve, a revise or a resume was lost).

**Check and do.**
1. Nothing is stuck: a signal is only a hint and every wait re-reads the row on a timer (`WAIT_RECONCILE_SECONDS`, one hour), so the run continues within the hour.
2. The fault is on the API side (it writes the row, then signals). Check the API's logs for failed `signal` calls to Temporal.
3. To avoid the wait, the API can re-send the signal; it is idempotent.

**Do not.** Do not change `WAIT_RECONCILE_SECONDS` in the environment: it is workflow-affecting and lives in `tuning.py`.
