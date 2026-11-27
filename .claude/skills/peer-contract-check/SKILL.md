---
name: peer-contract-check
description: Background rule for code that calls or consumes another service (the API service, IAM, Tender, the Model Gateway, the Temporal cluster, the engine). Peer contracts are assumptions until the peer's own doc confirms them.
user-invocable: false
---
Before changing a peer call, run input, signal payload, queue or engine function:
1. Read the peer's entry in `docs/worker-pool-contract.md` (what the API service sends and relies on) and `docs/worker-pool-questions-for-teams.md` (answers received and open items), and the peer's own document if one exists. For the engine, read `generation/tests/contract/engine_api_v1.json` and `docs/adr/0003-engine-api-gaps.md`.
2. Cite the document and section for any field, name or status you rely on. If there is none, say it is an assumption.
3. Record new or changed assumptions in `docs/worker-pool-questions-for-teams.md` (a team's open question) or an ADR; never harden a guess into a fact in code comments or docs.
4. Keep the service agnostic of the peer's internals; read only the fields we use. A missing or unknown field fails closed.
