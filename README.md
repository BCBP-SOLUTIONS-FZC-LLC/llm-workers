# llm-workers

Worker Pool of the LLM subsystem: durable Temporal workflows for long-form document generation and
extraction, with LangGraph section graphs, quota-aware pause and resume, and tenant-scoped retrieval.

* **Design:** [`docs/lld/llm-lld-workers-service.md`](docs/lld/llm-lld-workers-service.md) (rev 0.11). **This file is the source of truth for the Worker Pool design; changes to it are made here, in this repository.** The sibling LLD (`llm-retrieval-generation`, rev 2.144) is cited by section number, not copied
* **Decisions and deviations:** [`docs/adr/`](docs/adr/) (read ADR-0003 and ADR-0006 first)
* **Module map:** [`docs/module-map.md`](docs/module-map.md)
* **Contract with the API service:** [`docs/worker-pool-contract.md`](docs/worker-pool-contract.md)

## Layout

```
generation/src/llm_workers_generation/   workflows, activities, graphs, codec, observability, versioning, operator
generation/tests/                         unit, workflow (time-skipping), replay, security, integration, contract
deploy/                                   Dockerfile and the Helm chart (one Deployment per queue)
```

No API routes live here. The API service starts and signals workflows; every read and write goes through
`rag.engine` (no SQL, no `boto3`, no `asyncpg`, no `qdrant-client`).

## Develop

```bash
make setup      # .venv, uv sync --locked, .env from .env-example, git hooks (needs ssh access to the engine repo)
make ci         # lock-check + fmt-check + lint + typecheck + arch + replay + metrics-lint + chart-check + test-ci + build
make help       # every target
```

`docker compose up -d` starts a Temporal dev server (:7233, UI :8233) and nothing else. The database, caches and
model stub the engine needs come from the source repo: run `make docker-up` and `make gateway-up` in
`llm-retrieval-generation`.

Without the engine installed (it needs Python 3.15) the suites run against `generation/tests/fakes/fake_engine.py`.
Where each LLD rule is built and tested: [`docs/lld-traceability.md`](docs/lld-traceability.md).
Contributing, versioning and architecture: [`CONTRIBUTING.md`](CONTRIBUTING.md), [`VERSIONING.md`](VERSIONING.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md), [`CHANGELOG.md`](CHANGELOG.md).
