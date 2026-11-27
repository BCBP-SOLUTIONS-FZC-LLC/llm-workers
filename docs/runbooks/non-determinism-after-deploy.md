# A non-determinism failure after a deploy

**What you see.** Workflow tasks fail repeatedly after a new build went out; runs stop advancing (the failure is on the workflow task, not the run).

**Check and do.**
1. Find the build: the build id is the image tag. The failing workflows are pinned or auto-upgraded to it.
2. The cause is a workflow-code change without a patch marker. `make replay` should have caught it; check that CI ran.
3. Roll the worker build back (`worker-rollback.md`). Workflow tasks resume against the old code; nothing is lost.
4. Then fix: add `workflow.patched("<id>")`, register it in `versioning/patches.py`, record a history that covers it, and re-deploy.

**Do not.** Do not terminate the workflows to clear the error: the run's row is the truth and the API can restart it, but rolling back costs nothing.
