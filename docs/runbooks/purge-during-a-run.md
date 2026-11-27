# A purge during a run

**What you see.** A tender or tenant is purged while its run is live.

**Check and do.**
1. The API sends a best-effort `cancel` first, then deletes the rows. A run the cancel did not reach finds its row gone on the next write: the engine raises `RunGone` and the workflow ends quietly (`llm_workers_run_gone_total`).
2. Where the purge empties access scope first, the run ends `scope_revoked` (`llm_workers_scope_revoked_total`).
3. Neither is a fault and neither pages.

**Do not.** Do not try to recover a purged run.
