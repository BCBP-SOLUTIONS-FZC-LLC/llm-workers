# ADR-0003: what the engine's public API did not offer, and what is still open

**Status:** gaps 1 to 5 **closed** by the re-pin to `engine-v1.1.0` (this repository, workers LLD rev 0.8).
Gap 6 (the metrics identity) is **open**, pending engine 1.2.0, which is not merged or tagged.

## The pin

`generation/pyproject.toml` pins `tag = "engine-v1.1.0"`. It is an **annotated** tag: the tag object is
`047d834`, and it peels to `b6b92e6`, the squash merge on the engine's `main`. `uv.lock` locks
`b6b92e67031fa0f0e39656923c0f73682aea0ba0`. **Never pin `3b93dac`**: it is the branch commit the squash merge
orphans. `generation/tests/contract/engine_api_v1.json` is byte-equal to the file at the tag (27 functions,
`engine_api_version` 1.1.0; sha256 `8610f739...`), and `unit/test_engine_contract.py` compares it with the
installed engine's own snapshot.

## The gaps

Reading `rag.engine` 1.0.0 against the LLD found six gaps. Under 1.0.0 the adapter failed closed
(`EngineApiGap`, a non-retryable `Internal` error) instead of guessing.

| # | Need | Where used | What 1.0.0 had | Engine 1.1.0 | Here |
|---|---|---|---|---|---|
| 1 | The pack's outline per capability, with foundation flags | `outline` (LW-9) | no outline field | `DomainProfile.outline_skeletons: {capability: tuple[SkeletonSection(ref, section_type, is_foundation)]}` | **Closed.** `RagEngineApi.outline_entries` reads it. A pack with none for the capability is `PackOutlineMissing` (non-retryable `Internal`) |
| 2 | The pack's document-wide fact keys | `capture_facts`, `coherence_pass` (WP-16) | none | `DomainProfile.fact_keys: tuple[str, ...]` | **Closed.** `fact_keys` reads it; empty is a pack with none |
| 3 | A written section's text, in memory | facts, coherence, the refiner node | no reader | `rag.engine.load_section_text(ctx, section_id) -> str` | **Closed.** `section_text` calls it (see the decision below) |
| 4 | Public prompt-chunk, evidence and request types | `compose_prompt`, `verify`, `save_section` | internal modules only | `PromptChunk`, `EvidenceChunk`, `VerificationRequest`, `VerificationResult`, `ComposedPrompt` exported | **Closed.** The adapter builds the engine's own types; the look-alike dataclasses are gone (pinned by `test_the_adapter_builds_the_engines_own_types_not_look_alikes`) |
| 5 | `fail_run` from `suspended`, `pending` and `outline_building` | the 30-day suspension timeout (§9.1 step 5), a failure before the gate, an empty scope in those states | `RUN_TRANSITIONS` allowed `failed` only from `generating` and `awaiting_outline_approval`; `fail()` raised `InvariantViolation`, an untyped error | `failed` is reachable from every non-terminal state | **Closed.** The workflow records the failure (`GenerationFailed`) instead of ending `UnrecordedFailure` |
| 6 | Metrics identity: the engine registers its metrics as `llm_retrieval_generation_*` | `build_engine` inside a worker | fastapicommon allows one identity per process: the names are refused under `llm-workers` (`ValueError`) | **Not in 1.1.0** | **Open.** `build_rag_engine` passes `enable_metrics=False`; `test_real_engine.py` pins the `ValueError`; the engine's own metrics are off in workers (logs and traces stay on) |

The two pack keys, `outline_skeletons` and `fact_keys`, are an **assumed contract with Tender**: the names in the
pack JSON are this repository's proposal, written into the engine at 1.1.0, and Tender has not confirmed them.
It is an assumption, not a fact (`docs/worker-pool-questions-for-teams.md`).

## What was removed in the adoption

* The `getattr` reads and the `EngineApiGap` raises for gaps 1 to 3 in `activities/engine_api.py`.
* `ChunkView`, `EvidenceView` and `VerifyView`: `chunk_view`, `evidence_view` and `verify_request` return
  `PromptChunk`, `EvidenceChunk` and `VerificationRequest`.
* The test double `strict_fail_edges`. The fake's `fail_run` now checks the **real engine's**
  `RUN_TRANSITIONS`, so a run state with no `failed` edge raises as the engine does.
* `EngineApiGap` stays (class, mapping in `_guard.py`, a test) for an addition the engine lacks. Nothing needs it
  at run time today.

## Decision: `ContentUnavailable` from `load_section_text` is retryable, bounded by the attempt count

The engine turns **any** failure reading a section's text into `ContentUnavailable`: a store outage, a
database error, a key outside the tenant prefix, bytes that are not text and a digest mismatch (ASV-4) all look
the same, and the error carries no cause by design (OBS-2). `RunGone` (the section belongs to another artefact,
or the row is erased) is a normal result and is unchanged.

* **Retryable, not non-retryable.** If it were non-retryable, one transient S3 or database blip would fail the
  section, and a foundation section would then fail the run. The error cannot tell a blip from a permanent
  fault, so the safe default is to retry.
* **Bounded.** The activities that read the text (`write_section`, `capture_facts`, `coherence_pass`) run under
  a policy of 3 attempts (`tuning.ACTIVITY_MAX_ATTEMPTS`). A fault that outlasts them, a digest mismatch say,
  ends as a recorded `internal` failure: the section is closed `failed` (stage 2) or the run is failed
  (foundation, facts, coherence), never left unrecorded.
* **Content-free.** It leaves the activity as the fixed `Internal` error with no cause and no details
  (`activities/_guard.py::code_for`), so the §4.4 error table is unchanged. A distinct error type would let an
  alert tell a read failure from a bug; it is a later option that needs a §4.4 row, not part of this adoption.
* **Pinned by:** `unit/test_engine_adapter.py::test_content_unavailable_is_retryable_and_content_free`,
  `workflow/test_failure_paths.py::test_a_blip_reading_a_sections_text_is_retried_and_the_run_completes` and
  `::test_a_persistent_read_failure_ends_as_a_recorded_internal_failure`.

## What "real engine" means in these tests

* **In-process suites** (`unit`, `workflow`, `integration`): `rag.engine` is replaced by `fakes/fake_engine.py`
  (`conftest.py`). No database and no object store. The fake mirrors the real run state machine
  (`rag.domain.generation.run.RUN_TRANSITIONS`, imported from the installed engine).
* **Subprocess tests against the installed engine** (`unit/test_real_engine.py`, `unit/test_engine_contract.py`):
  pure and in-process in a child interpreter, no database. They check the shape snapshot, every delegated
  signature, the pack fields, the exported types, the `failed` edges, that the adapter builds the engine's own
  types, and the gap-6 `ValueError`.
* **Against real Postgres, through the workers' adapter:** `make real-engine-test` (`generation/real_engine/`,
  Docker, not in `make ci`, about 6 s once the images are pulled; the engine tag is cloned into
  `.cache/engine-tag` for its migrations). It starts, per run and on random localhost ports, Postgres 18
  (`uuidv7()`), floci (an S3 API) and Valkey (the pack cache) through the docker CLI, applies the tag's
  migrations in production's order (outbox first, then `llm_corpus` and `llm_generation`), and builds the engine
  with `build_rag_engine` (`enable_metrics=False`) as the non-superuser `rag_activity_login`. Per path:

  | # | Path | Result |
  |---|---|---|
  | 1 | `begin_stage`, `run_state` | **Ran for real** (Postgres) |
  | 2 | `fail_run` from `pending`, `outline_building`, `suspended` (run and artefact `failed`, one `GenerationFailed` in the outbox, `suspended_at` cleared) | **Ran for real** |
  | 3 | `save_outline`, `save_section`, `section_text` round trip | **Ran for real**, text in a real S3 API (floci); the object is listed in the bucket |
  | 4 | another artefact's section is `RunGone`; the engine's S3 class pointed at a closed port is `ContentUnavailable` and `capture_facts` ends as the fixed `Internal` error (retryable, no cause, no content); tampered bytes are `ContentUnavailable` | **Ran for real**; the dead store is the engine's own S3 class with a dead endpoint, Postgres real |
  | 5 | the pack keys through `load_pack`, `outline_entries`, `fact_keys` | **Ran with a Tender stub** (an HTTP server): the engine's resolver, Valkey cache and pack codec are real |
  | 6 | tenant isolation | **Ran for real** as a non-superuser: no tenant bound sees zero rows; tenant A's activity gets `RunGone` for tenant B's section |

  **Not run:** Temporal with the real engine (the workflows still run on the fake), the IAM and Model Gateway
  calls (`reresolve`, `call_model`, `verify`), Qdrant and retrieval, and the facts, coherence and mint writes on
  real Postgres. The engine's own tests for those are the engine repository's.

**Where the real engine behaved differently from the fake** (each paths the real test exercised):

* `load_section_text` of a section with no version is `""`; the fake returns text for any section id.
* A section of another artefact or tenant is `RunGone` (RLS hides the row); the fake has no tenants.
* `begin_stage("outline")` also moves the artefact `pending` to `generating`, and `fail_run` moves it to `failed`;
  the fake tracks the run row only.
* `save_section` needs a real retrieval set row and a real `VerificationResult` (foreign key, digest); the fake
  accepts anything.
* A tampered object (its bytes no longer hash to the stored digest) is `ContentUnavailable`.
* **A malformed pack, and a Tender outage, are not errors** (next section).

## Engine finding, open: `load_pack` hides a Tender outage

Not worked around. **Call:** `rag.engine.load_pack(ctx)` for a run whose pack Tender cannot serve (the stub answers
HTTP 503 after the resolver's two retries). **Input:** `domain_profile_version = "dp-1"`, the tenant's pack.
**Output:** no exception; `resolve_or_default` returns the platform default profile (`PackSource.DEFAULT`) with an
empty `outline_skeletons`. The adapter cannot tell that from a pack with no outline, so `outline` raises
`PackOutlineMissing` (non-retryable) and the run fails `internal`, where a retry once Tender is back would have
worked. A malformed `outline_skeletons` shape is downgraded the same way (`PackUnavailable` inside the
engine). **Pinned by** `real_engine/..::test_7_a_tender_outage_is_a_silent_default_pack_...` (last in the file:
the resolver's circuit breaker stays open afterwards). **For the owner of the engine and this repository to
decide:** the adapter could treat a `DEFAULT`-source bundle as a retryable pack-unavailable error (it can read
`bundle.source`), or the engine could raise instead of downgrading when the run pinned a version. Nothing changed
here.

## When the engine's metrics are turned on (engine 1.2.0, not adopted)

Future note, nothing here is built. **1.2.0 needs the fastapicommon metrics identity initialised before
`build_engine`**, and `build_engine` then takes the caller's identity. When `enable_metrics` goes `True`, the
8080 scrape gains about **34 engine families** named `llm_workers_*`, plus eventcommon's `platform_*` outbox
families. That changes the 8080 `check` and `--strict` assertions of `test_metrics_lint.py` (which pin the
11 activity-side families and the library's `platform_*` proposals) and the classification table in
ADR-0006. The 9464 `temporal_*` findings are not affected. Until then `enable_metrics=False` stays
(`build_rag_engine`), and `test_real_engine.py` keeps the `ValueError` pinned.
