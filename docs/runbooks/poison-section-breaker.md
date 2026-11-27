# The poison-section breaker

**What you see.** `llm_workers_poison_breaker_total` rises: most ended sections of a document failed with the same error, so the workflow stopped the document instead of retrying every section.

**Check and do.**
1. The run fails with that error's reason (for example `inference_unavailable`). Find the error type in `llm_workers_activity_failures_total`.
2. The cause is usually shared: the Gateway, a bad pack, the store. Fix it, then regenerate through the API.

**Do not.** The thresholds (`POISON_MIN_SECTIONS` and the share) are starting values; do not change them in the moment.
