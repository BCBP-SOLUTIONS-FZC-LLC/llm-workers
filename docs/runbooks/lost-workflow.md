# A lost workflow (the run's row says running, no workflow exists)

**What you see.** A run is `generating` or `suspended`, but Temporal has no open workflow for it (terminated, expired, or a start that never happened).

**Check and do.**
1. Find the workflow: the id is the artefact id. `temporal workflow describe -w <id>` in the right namespace.
2. The row is the truth. Restart the run with the API's restart route (W-8): the new workflow reads the row, skips finished sections and continues the current stage.
3. A gated run that is restarted re-arms its gate from the row; one whose gate passed fails at once with `gate_expired`.

**Do not.** Do not start workflows by hand. Only the API service starts them.
