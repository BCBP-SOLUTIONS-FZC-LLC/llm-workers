---
name: Feature request
about: Propose a new workflow behaviour, activity, signal, queue or operational capability
title: '[FEAT] '
labels: enhancement
assignees: ''
---

## Problem / motivation
What problem does this solve? Which runs or operators are affected?

## Proposed solution
Describe the behaviour change you'd like.

## Affected areas
- [ ] Workflow (a change to the commands it issues needs a patch marker)
- [ ] Activity or the section graph
- [ ] Signals or the run input (a contract with the API service: `docs/worker-pool-contract.md`)
- [ ] Task queues or Worker Deployments
- [ ] Engine API (needs a change in `llm-retrieval-generation`: see `docs/adr/0003-engine-api-gaps.md`)
- [ ] Metrics, alerts, runbooks
- [ ] Helm / deployment config

## Contract impact
Does the API service, Tender, IAM or the Workflow team need to change something first? Name the LLD section or open item (`docs/lld/llm-lld-workers-service.md` §20.3).

## Alternatives considered
Other approaches you evaluated and why you ruled them out.

## Acceptance criteria
- [ ]
- [ ]
- [ ]

## Additional context
Links to related issues, LLD sections, ADRs.
