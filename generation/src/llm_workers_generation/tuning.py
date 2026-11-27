"""Workflow-affecting numbers (LLD §5.6, §6.1, §8.2, §9.1, §12.1, §16). Standard library only.

These live in code and not in the environment on purpose. A workflow reads them while it is
replayed, and a value that changes between two builds would change the commands a running
history produces. Changing one is a code change: it goes through review, and where it alters
commands it needs a ``workflow.patched`` marker (WV-1, ADR-0004). Process-level values that
no workflow reads (concurrency, shutdown, URLs) are in ``config.Settings``.

Replay safety: pure constants. Security: no content.
"""

from __future__ import annotations

# Timeouts, heartbeats and retries (§5.6, WP-13)
ACTIVITY_START_TO_CLOSE_SECONDS = 600
ACTIVITY_HEARTBEAT_SECONDS = 30
ACTIVITY_MAX_ATTEMPTS = 3
OUTLINE_START_TO_CLOSE_SECONDS = 300  # outline, capture_facts, coherence_pass
CONTROL_START_TO_CLOSE_SECONDS = 30
CONTROL_WRITE_MAX_ATTEMPTS = 5
# acquire_slot, the slot calls (the run_state read has a window instead)
CONTROL_READ_MAX_ATTEMPTS = 3
# run_state is retried this long before the run fails (a store outage is not a failed run)
READ_RETRY_WINDOW_SECONDS = 3600

# Fan-out (§12.1). Per tenant is enforced by TenantSlots, global by the fleet cap.
FANOUT_MAX_PER_DOCUMENT = 5
FANOUT_MAX_PER_TENANT = 10
FANOUT_MAX_GLOBAL = 100
TENANT_SLOT_LEASE_SECONDS = 2100  # ACTIVITY_MAX_ATTEMPTS * START_TO_CLOSE + 300 backoff
# one acquire update waits this long inside TenantSlots, then answers "not granted"
ACQUIRE_POLL_SECONDS = 2
# the activity waits this much longer than the update before it gives up
ACQUIRE_CLIENT_MARGIN_SECONDS = 5
# the gate's pause between reads once its deadline has passed and the row has not moved
GATE_RECHECK_SECONDS = 5
ACQUIRE_BACKOFF_MAX_SECONDS = (
    15  # the workflow sleeps (not an activity) between asks, doubling to this
)

# Waits (§8.2, §8.5, §9.1)
WAIT_RECONCILE_SECONDS = 3600
SUSPEND_MAX_DAYS = 30
REVIEW_GATE_DAYS = 30  # tenant-configurable in the engine; here only for the patch-retention rule

# Run bounds (§6.1, WW-3, WW-4, §13.2)
RUN_MAX_SECONDS = 21600
POISON_THRESHOLD_PERCENT = 50
POISON_MIN_SECTIONS = 6
TERMINAL_WRITE_RETRY_MINUTES = 60

# Section graph (WA-12, ASV-2): retries after a weak verification
SECTION_MAX_AUTO_RETRIES = 2

# Failure reasons the workflow can write with fail_run (sibling enum, §4.4)
FAILURE_REASONS = frozenset(
    {
        "internal",
        "inference_unavailable",
        "artefact_store_unwritable",
        "quota_exhausted",
        "scope_revoked",
        "residency_capacity",
        "gate_expired",
        "schema_violation",
        "tenant_suspended",
    }
)

TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})
