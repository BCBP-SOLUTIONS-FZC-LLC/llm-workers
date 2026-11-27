---
name: Bug report
about: Report a defect in llm-workers (a workflow, an activity, the section graph, the codec, deployment)
title: '[BUG] '
labels: bug
assignees: ''
---

## Description
A clear description of the bug.

## Service version
`github.com/BCBP-SOLUTIONS-FZC-LLC/llm-workers` — tag / commit SHA, and the image tag / Worker Deployment build id:

## Python version and engine version
`python --version`, and the pinned `retrieval-generation` ref in `generation/pyproject.toml`

## Environment
- [ ] Local dev (docker compose Temporal dev server)
- [ ] Staging
- [ ] Production

## Affected area
- [ ] GenerationWorkflow (gate, stages, quota suspension)
- [ ] SectionWorkflow / section graph
- [ ] ExtractionWorkflow
- [ ] TenantSlots (fan-out per tenant)
- [ ] Activity (name: )
- [ ] Payload codec / failure converter
- [ ] Task queue / deployment / scaling
- [ ] Replay / non-determinism error after a deploy

## Identifiers
Workflow id, run id, generation run id, trace id. **Do not paste prompts, chunk text, generated text or a reviewer's instruction.**

## Steps to reproduce
1.
2.
3.

## Expected behaviour
What you expected to happen.

## Actual behaviour
What actually happened. Include the failure type (the `ApplicationError` type), the run's status and failure reason, and log lines (identifiers only).

## Additional context
Temporal server version, namespace, related issues.
