# A quiet async run

**What you see.** An asynchronous run shows no progress for longer than `QUIET_ASYNC_RUN_SECONDS`; the API's `quiet-async-runs` sweep exits 2. Also `llm_workers_run_deadline_total` for a run that passed the 6-hour deadline (`RUN_MAX_SECONDS`).

**Check and do.**
1. The sweep lists the run. Look at its workflow: is it open, waiting on a signal, suspended, or retrying an activity?
2. A run past its deadline is failed by the workflow itself; a quiet one with no workflow is a lost workflow (`lost-workflow.md`).
3. A draining pod leaves its workflow RUNNING for another pod to continue: that is normal during a rollout, not a fault.

**Do not.** Restart through the API, never by editing rows.
