# ADR-0005: `TenantSlots` by update-with-start, with a polling acquire

**Status:** accepted. **LLD:** §12.2 (LW-8), WW-2, WW-5.

`acquire` is an update with a validator; `TenantSlots` queues request ids first in, first out, grants up to
`FANOUT_MAX_PER_TENANT`, reaps expired leases, and continues as new only when Temporal suggests it, after
its handlers finish, carrying leases and queue order. State is built in `@workflow.init`, because with
update-with-start the first handler can run before `run` starts (found by a test).

**Refinement of the LLD:** the `acquire` update waits at most `ACQUIRE_POLL_SECONDS` (**2 s**) *inside
`TenantSlots`* and answers `granted=False` (rev 0.10: it was the activity that gave up after 2 s and left the
update handler waiting, one more per poll); the activity allows a margin on top; the section child then **sleeps on a workflow timer** (1 s doubling to 15 s) and asks again,
keeping its place (the request id is stable). Waiting therefore never holds a work slot or one of the 16
control slots, which must stay free for approvals and terminal writes (LW-16, G-6). An earlier draft held
the control slot for 20 s per poll; with about 16 queued sections that starves the control queue and trips
the "control queue backlog" page. Cost: about five events per poll on the child's history, so a ten-minute
wait is about 200 events. `release_slot` runs in a `finally` and also withdraws a waiting request. A
cancelled child is never recorded as a failure (`is_cancelled_exception` is re-raised, not mapped), and its
slot is released before the parent ends (test: cancel during stage 2). The bound is per queue, not across
queues (WOQ-10).
