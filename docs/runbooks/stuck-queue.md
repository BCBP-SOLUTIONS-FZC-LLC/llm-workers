# A stuck queue

**What you see.** Tasks wait on `llm-generation*` or `llm-extraction*` (backlog age above 5 minutes, schedule-to-start rising); sections do not start.

**Check and do.**
1. Pods: are the Deployments for that queue running and ready (`/ready` on the `ops` port)? A pod that cannot reach Temporal or Postgres is not ready.
2. Slots: `temporal_worker_task_slots_available` at 0 means every slot is busy: the fleet cap (`MAX_REPLICAS` x `MAX_CONCURRENT_ACTIVITIES`) is reached. That is capacity, not a fault; scale (KEDA) or wait.
3. A quota storm (`llm_workers_quota_suspensions_total` rising) parks runs, it does not block the queue: see `quota-suspension-storm.md`.
4. The control queue (`<queue>-control`) is separate on purpose. If a control task waits, approvals and terminal writes are stuck: page.

**Do not.** Scale the queue's Deployment, or fix what keeps pods unready. Do not purge a queue.
