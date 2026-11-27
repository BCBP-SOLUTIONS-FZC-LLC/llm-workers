# A database outage

**What you see.** `Internal` and `StoreUnwritable` activity failures rise; `/ready` may flap; terminal writes retry.

**Check and do.**
1. Control reads (`run_state`) are retried for an hour before a run is failed; terminal writes (`fail_run`, `complete_run`) are retried for 60 minutes (`TERMINAL_WRITE_RETRY_MINUTES`).
2. Within those windows runs wait and then continue by themselves. Beyond them a run ends `UnrecordedFailure` (`unrecorded-failure.md`).
3. Check PgBouncer, the `rag_activity` login, and the secret that holds the connection strings. A permission error from the role is a page.
4. Workers hold no database state of their own.

**Do not.** Do not restart all workers at once during the outage: each restart drops in-flight activities, which then retry.
