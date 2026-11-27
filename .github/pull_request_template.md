## Description
Provide a clear description of the changes.

---

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Refactor
- [ ] Documentation
- [ ] Test
- [ ] Breaking change
- [ ] Workflow change (changes the commands a workflow issues)
- [ ] Engine pin change (`generation/pyproject.toml`)

---

## Testing
- [ ] Unit / workflow (time-skipping) tests added/updated (`make test`)
- [ ] Architecture rules and engine contract pass (`make arch`)
- [ ] Replay passes (`make replay`)
- [ ] Manual testing performed (if required)

---

## Checklist

### Code Quality
- [ ] Formatted (`make fmt-check`), linted (`make lint`), type-checked (`make typecheck`)
- [ ] No debug logs / commented-out code
- [ ] No secrets or DSNs hardcoded
- [ ] `CHANGELOG.md` updated (`generation/src/` or `deploy/helm/` changes)

### Workflows and replay
*Complete only when `generation/src/llm_workers_generation/workflows/` changed.*
- [ ] No I/O, no wall clock, no randomness in workflow code (WD-1, WDT-1..5)
- [ ] A change to the order or number of commands is behind a `workflow.patched` marker, registered in `versioning/patches.py` (WV-1)
- [ ] Activity, signal and workflow names are unchanged (WV-2)
- [ ] A time-skipping test covers the new path, and a recorded history replays

### Security
- [ ] Nothing readable goes into Temporal history: input, signals, results, failures, heartbeats (`generation/tests/security/test_no_content_in_history.py` passes)
- [ ] Every table read or write goes through `rag.engine`; no SQL, no direct S3, Qdrant or HTTP client
- [ ] Logging, metrics and tracing only through the shared libraries; no tenant, user or document id as a metric label

### Engine boundary
*Complete only when the engine pin or `activities/engine_api.py` changed.*
- [ ] `uv.lock` updated (`make lock`), contract tests pass against the new engine
- [ ] `docs/adr/0003-engine-api-gaps.md` updated if a gap closed or opened

### Documentation
- [ ] Docs / LLD / `docs/lld-traceability.md` updated if a rule's status changed

---

## Related Issue
Closes #<issue-id>

---

## Deployment Notes
Anything operators need to know (new env vars, queue or deployment changes, a worker build that must stay alive for pinned runs).
