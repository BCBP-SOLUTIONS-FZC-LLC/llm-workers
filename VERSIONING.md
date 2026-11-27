# Versioning and releases

This repository is a **deployed Python worker service**, not a library other services `pip install`. It consumes one versioned API, `rag.engine` (with the stdlib-only `llm_models.workers`) from `llm-retrieval-generation`, and it exposes a runtime contract to the API service, the Temporal cluster and operators. **`rag.engine` is pinned by git tag**: `ENGINE_API_VERSION` follows SemVer in the engine repository, `src/rag/engine/engine_api_v1.json` there records its shape, and `generation/tests/contract/engine_api_v1.json` here is the copy this repo is tested against. What gets versioned here is the **container image** (`ghcr.io/bcbp-solutions-fzc-llc/llm-workers-generation`, planned: see [Planned](#planned-not-yet-in-place)) plus that runtime contract. Versions are published with **Git tags** and described in [CHANGELOG.md](./CHANGELOG.md).

This document follows the same SemVer / image-tag / maintainer-process layout as `llm-retrieval-generation/VERSIONING.md`. Where this service has no mechanism yet (a published image, a deploy gate, an engine tag), the section says so instead of describing it as if it existed. Every such item is marked **Planned** and tracked in one table below.

## The engine pin

The engine is a private git dependency declared in `generation/pyproject.toml`:

```toml
[tool.uv.sources]
retrieval-generation = { git = "ssh://git@github.com/BCBP-SOLUTIONS-FZC-LLC/llm-retrieval-generation.git", tag = "engine-vX.Y.Z" }
```

- **Target:** an engine release tag (decision H-8 in the LLD). WB-2 requires an exact pin; WB-3 keeps a removed engine function for at least 60 days so a build that started a run still finds it.
- **Today:** `tag = "engine-v1.1.0"` in `generation/pyproject.toml`: an annotated tag (tag object `047d834`) that peels to `b6b92e6`, the squash merge on the engine's `main`; `uv.lock` locks `b6b92e67031fa0f0e39656923c0f73682aea0ba0`. Never pin `3b93dac` (the orphaned branch commit). Engine 1.2.0 (ADR-0003 gap 6) is not merged or tagged and is not adopted.
- **Bumping it** is a reviewed change: `make lock`, then `make arch` (the engine contract test compares the installed engine's snapshot with `generation/tests/contract/engine_api_v1.json`) and `make test-ci`. A scheduled job that opens this pull request when a new engine tag appears (LLD §4.6) is **planned**, not built.
- The build id of a Worker Deployment is the image tag and carries the engine tag it pins, so a run keeps the engine version its worker started it on.

## Semantic versioning (SemVer)

We use [SemVer 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH` (e.g. `v1.2.3`).

| Bump | When you change | Examples |
|------|-----------------|----------|
| **MAJOR** | Breaking change in the runtime contract (below) | Renaming or removing a workflow, signal or activity name, a task queue name, a run-input or signal field the API service sends, a metric name, a required env var; a change to workflow code without a patch marker that breaks histories of runs still open; moving to an engine major version |
| **MINOR** | New backward-compatible capability | A new activity or workflow, a new optional signal field, a new queue or search attribute, a new `llm_workers_*` metric, a new optional env var, an additive engine bump |
| **PATCH** | Backward-compatible fix | Bug fix, performance improvement, dependency bump with no observable change, documentation-only correction, a workflow change behind a patch marker that does not alter behaviour |

### What counts as the runtime contract

| In scope (SemVer applies) | Out of scope (may change without MAJOR) |
|---------------------------|------------------------------------------|
| Workflow type names (`GenerationWorkflow`, `SectionWorkflow`, `ExtractionWorkflow`, `TenantSlots`), signal names (`approve_outline`, `revise_outline`, `cancel`, `resume`), activity names (the 15 in LLD §7.2, plus `acquire_slot` and `release_slot`), workflow ids (`{artefact_id}`, `regen:{base_version_id}`, `{parent}:s:{section}:{stage}`, `slots:{queue}:{tenant}`). **Never renamed** (WV-2) | Python module and class names, file layout: nothing outside this process imports them |
| The six work queue names and their `-control` queues; the Worker Deployment name (the queue) | Pod counts, replica bounds, resource requests |
| The run input and signal payload shapes: owned by `llm_models.workers` in the engine distribution, so a change there is an engine change first | Activity argument and result types in `types.py`: internal between the workflow and the activities, but changing one needs a patch marker |
| `llm_workers_*` metric names (dashboards and alerts key off them); the search attributes `RunKind`, `ArtefactId`, `RunStatus` once the operator registers them | Histogram buckets, label cardinality beyond what is documented, SDK-emitted metric names |
| Env var **names and semantics** in `.env-example` (`WORKER_KIND`, `WORKER_TIER`, `TEMPORAL_*`, `PAYLOAD_CODEC_*`, ...) | Env var **defaults** where the LLD says they are starting values |
| `/health`, `/live`, `/ready` and `/metrics` on `HEALTH_PORT` (the ops server): existence and meaning | `tuning.py` numbers that do not change commands (a change that does needs a patch marker) |

### Replay safety is part of the contract

A workflow can sit at its gate for 30 days and then be suspended for up to 30 more, so workflow code changes while runs are alive. Any change to the order or number of commands needs `workflow.patched("<id>")`, registered in `generation/src/llm_workers_generation/versioning/patches.py`; `make replay` and the CI replay job replay recorded histories against the new code. A marker is removed only after `retention_days()` (about 60 days at the defaults) with no older run open (WV-4). `GenerationWorkflow` and `TenantSlots` auto-upgrade to the newest build; `SectionWorkflow`, `ExtractionWorkflow` and the activities stay pinned to the build that started them (WV-6). **No marker exists yet:** this is revision one of every workflow.

### Guarantees

- **Pre-`v1.0.0` (current status):** per [SemVer §4](https://semver.org/#spec-item-4), anything may change at any time while the major version is `0`. This service has never been deployed, so no stable contract has been committed to by a tag.
- **MAJOR (once `v1` ships):** we avoid breaking changes to the contract within `v1.x`.
- **MINOR:** safe to redeploy without changing queue names or env vars.
- **PATCH:** drop-in image replacement.

## Supported releases

| Version | Status | Image tag | Notes |
|---------|--------|-----------|-------|
| *(none tagged)* | **Unreleased** | n/a | [CHANGELOG.md](CHANGELOG.md) lives entirely under `Unreleased`. `pyproject.toml`'s `version` is `0.1.0`: bump it in lockstep with the first Git tag; it is not a published release. No live deployment, so no support window has started |

**This service is not yet safe to release even when `make ci` is green.** The blockers:

- The engine (`rag.engine` 1.1.0) closed ADR-0003 gaps 1 to 5; gap 6 (its metrics identity, engine 1.2.0) is open, so the engine's own metrics are off in a worker. The pack keys `outline_skeletons` and `fact_keys` are an assumed Tender contract. The workers' adapter and activities have not run against a real database ([`docs/adr/0003-engine-api-gaps.md`](docs/adr/0003-engine-api-gaps.md)).
- Nothing has run against a real Temporal cluster with Worker Versioning, a real database or the Model Gateway; peer contracts are the LLD's answers in §20.2 and open items in §20.3.
- No alert rules, SLOs or runbooks exist (LLD §15.4 to §15.6).

Resolve or consciously accept each before a tag is cut.

## Consume a release

This service is **not** consumed via `pip install`. It is consumed as a **container image**, one Deployment per task queue, started by the Helm chart in `deploy/helm/llm-workers/` (no pipeline applies it yet).

### Pull and verify the image

**Written, never run: `release.yml` builds, scans, signs and publishes the image on a version tag, but no tag exists.** Once one does:

```bash
docker pull ghcr.io/bcbp-solutions-fzc-llc/llm-workers-generation:v0.1.0
cosign verify \
  --certificate-identity-regexp "^https://github\.com/BCBP-SOLUTIONS-FZC-LLC/llm-workers/.github/workflows/.*@refs/tags/.*$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/bcbp-solutions-fzc-llc/llm-workers-generation@<digest>
```

### Image tag scheme

| Tag pattern | Produced for | Example (from `v1.2.3`) |
|-------------|--------------|--------------------------|
| `MAJOR.MINOR.PATCH` | Every release | `1.2.3` |
| `MAJOR.MINOR` | Every release | `1.2` |
| `MAJOR` | Every release | `1` |
| `latest` | Stable releases only (no pre-release suffix) | `latest` |

Pushes to `main` publish `main` and `sha-<short>` tags. Pin production by a **release tag or digest**. The image tag is also the Worker Deployment **build id** (WV-5): a new build takes new runs, and an old build is retired only when no workflow pinned to it is open.

### Run

Each pod runs `llm-workers-generation` (`generation/src/llm_workers_generation/worker.py`): one work worker on `<queue>` and one activity-only worker on `<queue>-control`, with `WORKER_KIND` and `WORKER_TIER` selecting the queue. The engine reads its own database and cache settings, which the chart must provide from secrets.

## Maintainer release process

**Written, never run.** `release.yml` exists and `ci.yml` runs `validate-test`, `validate-quality`, `build-image`, `trivy`, `smoke` and (on pushes) `push`, but neither has run on GitHub, and no tag exists.

1. **Merge** all changes for the release to `main`. Confirm the blockers above are resolved or consciously accepted.
2. **Update `CHANGELOG.md`:** move `Unreleased` entries into a new `## [X.Y.Z] - YYYY-MM-DD` section. `release.yml` fails if the section is missing (`verify-changelog-entry.sh`).
3. **Bump `pyproject.toml` `version`** (root and `generation/`) and refresh the lockfile: `make lock && make lock-check`.
4. **Run `make ci` locally**, then confirm the engine pin is a **tag**, not a commit.
5. **Create and push an annotated tag**: `git tag -a v0.1.0 -m "v0.1.0" && git push origin v0.1.0`.
6. **The release workflow** (`release.yml`) runs: `validate-test` and `validate-quality` at the tagged commit; verify the tag matches HEAD and the CHANGELOG entry exists; build the image without pushing, CVE-scan it (CRITICAL/HIGH with a fix fails), smoke it; push; generate the SBOM and provenance; sign with Cosign; verify the signature; create the GitHub Release with the CHANGELOG section as notes.
7. **Notify consumers**: the API service owners (run input, signals, queues), the Workflow Engine team (namespace, Worker Deployments, search attributes), and whoever owns the deployment, with upgrade notes if MINOR or MAJOR.

Pre-release tags (`v1.1.0-rc.1`, `v1.1.0-beta.1`) match the release trigger and produce a signed, scanned image, never a `latest` tag.

## Compatibility matrix

| llm-workers | Python | Engine (`rag.engine`) | `temporalio` | LangGraph | Temporal server |
|---|---|---|---|---|---|
| Unreleased (`main`) | `>=3.15` (image 3.15.2; developed on 3.15.0) | 1.1.0, pinned by tag `engine-v1.1.0` | `>=1.37,<2` (see `uv.lock`) | `>=1.2,<2` (see `uv.lock`) | Worker Deployments needed: version not yet stated by the platform team (LLD PL-6, open) |

Other pinned tooling: `uv` 0.13.6 (Makefile, CI and the Dockerfile).

## Planned (not yet in place)

Anything marked **Planned** is the intended design, kept here so the process is agreed before the machinery exists. When an item lands, change its status to **Done**, remove the "Planned" wording where it appears above, and update this file in the same change.

| Item | What it will do | Status |
|------|-----------------|--------|
| Engine release tag | `rag.engine` pinned by tag, not commit (H-8) | **Done**: `engine-v1.1.0`, an annotated tag |
| Engine-bump pull request job | Open a PR here when a new engine tag appears, run the contract and replay gates (LLD §4.6) | Planned (no job exists) |
| Engine metrics identity (ADR-0003 gap 6) | `build_engine` takes the caller's metrics identity; then `enable_metrics=True` and a re-pin | Planned (engine 1.2.0, not merged) |
| Published image | `ci.yml` pushes `main` images; `release.yml` pushes release images, signed | **Written**, never run |
| `release.yml`, verify scripts, SBOM, provenance | Tag-triggered validate, build, scan, smoke, push, sign, release | **Written**, never run |
| Deploy gate | Live deploy, digest check, rollout status, error-rate check with rollback | Planned, undecided |
| Helm chart | One Deployment per queue, KEDA scaler, PDB, network policy | **Done** (`helm lint` and `helm template` run for dev, staging and prod values; never applied or schema-validated; the ScaledObject `scalerAddress` is empty until set; the network policy egress is open until PL-3 is answered) |
| Nightly replay of live histories | Export a sample (identifiers only), replay on `main` (LLD §18.2) | Planned (a stub step in an earlier `ci.yml` draft is not in the current one) |
| Alert rules, SLOs, runbooks | LLD §15.4 to §15.6 | Planned |
| Worker Versioning on a real cluster | Verify both queues join one deployment version (PL-6) | Planned |
| `BUILD_VERSION` / `BUILD_ID` from the tag | The pod reports the released build | **Done** in the Dockerfile (`ARG BUILD_VERSION` sets `BUILD_ID`); unexercised |
| Support-window policy | How long a superseded major gets security-only fixes | Planned (after the first `v1.0.0`) |

**Done today**: the SemVer rules, `CHANGELOG.md`, `changelog-check.yml`, the 95% coverage gate, `uv.lock` with `make lock-check`, the architecture and engine-contract tests, and the replay gate (`make replay`).

## Related files

| File | Purpose |
|------|---------|
| [CHANGELOG.md](./CHANGELOG.md) | History per version: currently entirely `Unreleased` |
| [docs/lld/llm-lld-workers-service.md](./docs/lld/llm-lld-workers-service.md) | The design: queues, workflows, the engine boundary, versioning (§5.5) |
| [docs/lld-traceability.md](./docs/lld-traceability.md) | Where each LLD rule is built and tested |
| [docs/adr/](./docs/adr/) | Decisions and deviations; ADR-0003 is the engine gaps |
| [docs/worker-pool-contract.md](./docs/worker-pool-contract.md) | What the API service sends and relies on |
| [.github/workflows/ci.yml](./.github/workflows/ci.yml) | The validation and image pipeline |
| [pyproject.toml](./pyproject.toml) / [generation/pyproject.toml](./generation/pyproject.toml) / [uv.lock](./uv.lock) | Version, dependencies, the engine pin |
