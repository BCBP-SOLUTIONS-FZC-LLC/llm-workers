# ADR-0004: workflow tunables are code, not environment

**Status:** accepted. **LLD:** §5.4 (WDT-3), §16.

A workflow reads `WAIT_RECONCILE_SECONDS`, `RUN_MAX_SECONDS`, fan-out bounds, timeouts and the poison
thresholds while it is replayed. A value that differs between two builds changes the commands a history
produces. They therefore live in `tuning.py` (reviewed, versioned with the build); `Settings` holds only
process-level values no workflow reads, and validates the §16 rules against `tuning`. Changing a `tuning`
value that alters commands needs a `workflow.patched` marker (`versioning/patches.py`). **Deviation from
§16**, which lists these as environment settings: reopen if operations needs to change them without a build.
