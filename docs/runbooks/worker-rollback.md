# A worker rollback

**What you see.** A new worker build misbehaves (non-determinism, restart loop, rising failures) and must be replaced by the previous build.

**Check and do.**
1. Workers use Worker Deployments (one deployment per queue, the image tag is the build id). Sections are pinned to the build that started them; the parent run auto-upgrades.
2. Make the previous build the deployment's current version (`temporal worker deployment set-current-version`), then scale the bad build down only after its pinned work has drained.
3. Keep the previous image: a build stays until the runs pinned to it can no longer be open (see `retention_days` in `versioning/patches.py`).

**Do not.** Do not delete a build that still has open pinned workflows.
