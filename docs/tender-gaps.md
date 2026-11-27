# What the LLM Worker Pool needs from Tender: gaps, status and how each was settled

**For:** the Tender team. **From:** the `llm-workers` repository (the Worker Pool that generates tender documents).
**Date:** 2026-11-27. **Status words:** *Open* (needs a Tender answer), *Closed* (settled, nothing to do), *Closed, provisional* (settled on Tender's earlier reply, which Tender said stands only until its HLD v0.10 is signed off).

Plain terms used below. **The pack** is the Domain Profile and Language Pack that Tender resolves for a tenant and a pinned version; the workers read it at run start. **A run** is one generation of one document. **A section** is one part of that document. **Foundation sections** are the ones written first (stage 1) whose facts the rest must agree with.

Nothing here is a quotation of Tender. Where a row says "Tender said", it is our summary of the reply we received on 22 November 2026 (Workflow Engine team, answering for Tender) and the 25 November 2026 reply on events.

## Summary

| # | Subject | Status | Needs from Tender |
|---|---|---|---|
| 1 | Where the document outline comes from | Closed, provisional | Sign-off of Tender HLD v0.10 |
| 2 | The two pack field names the workers read | **Open (assumption)** | Confirm or give the real names |
| 3 | What Tender does with a document that finished with failed or gap sections | **Open** | Tender's own decision and agreement |
| 4 | A regeneration instruction that contradicts an established fact | **Open** | Tender's agreement on what the caller sees |
| 5 | Progress to the browser no longer goes through Tender | **Open** | Confirm Tender no longer relays it |
| 6 | Workers calling Tender's pack and access-list endpoints | **Open** | Admit the `llm-workers` namespace; confirm the identity accepted |
| 7 | A Tender outage is silently treated as "no outline" | **Open (engine behaviour)** | Say what the pack endpoint returns when it cannot serve; help choose the fix |
| 8 | Who receives the LLM events | Closed | Nothing |
| 9 | The completeness verdict Tender sees | Closed | Nothing |
| 10 | Tender purge while a run is live | Closed (built) | Nothing |

## 1. Where the document outline comes from — Closed, provisional

**What it is.** The workers must decide which sections a document has, in what order, and which are foundation sections. We asked whether the outline should also be built by a model, or come only from the pack.

**What was decided.** The outline is taken from the pinned pack's skeleton for the run's capability: section types, order and foundation flag. The outline step makes no model call. Tender said the skeleton is all there is (Tender HLD section 7.5, decisions D21 and section 12.6).

**Basis.** Tender's reply of 22 November 2026. It is provisional: Tender said its answers stand only until its HLD v0.10 is signed off.

**Status.** Built and tested. If the skeleton is missing for a capability, the run fails with a fixed internal error and is not retried (the pack is wrong; retrying cannot help).

## 2. The two pack field names the workers read — Open (an assumption of ours)

**What it is.** The engine the workers use reads two fields from the pack JSON, under the Domain Profile:

* `outline_skeletons`: an object whose key is a capability and whose value is a list of sections, each `{ "ref": text, "section_type": text, "is_foundation": true or false }`. A list is capped at 200 sections.
* `fact_keys`: a list of non-empty texts naming the document-wide facts (for example a contract value or a date) that stage 1 captures and later sections must agree with. Capped at 500.

**What was missing.** The engine's first version had no such fields at all, so the workers could not get an outline or the fact keys. That engine gap is closed (engine version 1.1.0 reads both fields). But the **names and shape are our proposal**: the engine's own source says the pack contract file from Tender is not in its repository, and that the shape is "the assumed contract". No Tender document confirms them.

**Why it matters.** An absent field reads as empty. A pack that uses other names loads without error but gives no outline, and the run fails with "outline missing". A wrong name looks exactly like a missing one.

**Status.** Open. Our default is that the names stay as proposed.
**Asked of Tender:** confirm these two names and shapes for the Domain Profile, or give the names Tender uses.

## 3. A finished document with failed or gap sections — Open

**What it is.** A run can finish `complete` while some sections failed, or have too little evidence to be written (a "gap"). A stage-1 failure fails the whole run; a later-section failure does not.

**What we decided on our side.** The completion event carries two counts, `sections_failed` and `sections_gap`, so a consumer never treats a partial document as whole. The completeness verdict also appears on the generation-completed and regenerated events.

**What Tender said.** The handling is on the LLM side and Tender decides how to proceed (closed for us).

**Still open.** Tender's own agreement on what its closing step does with those two counts. Our default: Tender reads both counts and does not close a partial document.

## 4. A regeneration instruction that contradicts an established fact — Open

**What it is.** A reviewer asks for a regeneration whose instruction conflicts with a fact already fixed in stage 1 (for instance, a different contract value). No person can be asked in the middle of a run.

**What was decided.** The working rule (our reading of Tender's answer): keep the established value for that run, record the conflict, and show it on the finished document for a reviewer to resolve and regenerate. Tender said this goes to the caller.

**Still open.** Tender's agreement on exactly what the caller sees. Our default: the conflict is surfaced to the caller.

## 5. Progress to the browser — Open

**What it is.** The platform design said Tender relays run progress to the browser. The Workflow team decided on 25 November 2026 that progress instead reaches the browser through the Workflow Stream Gateway, fed by an event consumer the LLM subsystem runs itself. Tender no longer relays it.

**Status.** Closed from our side. Open until Tender confirms, and changes its own design to match. Not built yet; it blocks only the go-live of that consumer.

## 6. Calls from the workers to Tender — Open

**What it is.** Every section the workers (a) read the pack from Tender, and (b) call Tender's access-list check for the document, and also `llm-context`. They have no user request, so they authenticate as a service.

**Open on the platform side.**
* Which service identity and request headers the workers must send, and whether Tender accepts them.
* Whether Tender's network policy admits the `llm-workers` namespace. If not, the calls fail and, by design, the step fails closed rather than continue without an access check.

**Asked of Tender:** admit the namespace and tell us the identity and headers you accept. Shared with Platform and IAM; IAM has already said it plays no part in the worker-to-Temporal connection.

## 7. A Tender outage is silently treated as "no outline" — Open (found by testing)

**What we did.** We ran the workers' code against the real engine (version 1.1.0) with a stand-in for Tender, on a real database.

**Call and result.** The engine's `load_pack` was asked for a run whose pinned pack Tender could not serve (the stand-in answered HTTP 503, after the engine's two retries). The engine **did not raise an error**. It returned the platform's default profile, which has an empty outline. A pack with a badly shaped `outline_skeletons` is downgraded the same way.

**Effect.** The workers cannot tell an outage from a pack that truly has no outline. The outline step fails for good as "outline missing", where a retry once Tender is back would have worked.

**Status.** Open, deliberately not worked around, and pinned by a test. The decision belongs to the owner of the engine and this repository: either the workers treat a default-profile result as a retryable "pack unavailable" error, or the engine raises when a pinned version cannot be served.
**Asked of Tender:** what does the pack endpoint return when it cannot serve a pinned version (status code and body)? Does a missing pack differ from an outage?

## 8. Who receives the LLM events — Closed

Tender said tenant administrators do not receive LLM events; Tender names, in the `recipients` of each event, the users and departments who follow a run. All ten event types reach the browser as `llm.<type>`. Nothing is left to do here.

## 9. The completeness verdict — Closed

As built: stage-1 failure fails the run; a stage-2 failure leaves the run complete with that section failed; a section with no version counts as insufficient. Tender sees the verdict on the generation-completed and regenerated events.

## 10. A Tender purge while a run is live — Closed (built)

When a tender or tenant is purged, the API service sends a best-effort cancel to the live runs before deleting rows. If a run is not reached, its next engine write finds the row gone and it ends quietly with no page; where the purge empties access scope first, the run ends as scope-revoked. Nothing is needed from Tender beyond the purge event itself.

## Where this comes from in the repository

The questions file `docs/worker-pool-questions-for-teams.md` (Tender section and the closed table), the workers design document `docs/lld/llm-lld-workers-service.md` (outline derivation, completeness events, the open-items table), `docs/adr/0003-engine-api-gaps.md` (the engine fields and the outage finding), and the code in `generation/src/llm_workers_generation/activities/engine_api.py`.
