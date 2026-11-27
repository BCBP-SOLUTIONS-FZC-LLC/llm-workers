# A Model Gateway outage

**What you see.** `write_section` and the model steps fail with `InferenceUnavailable` (`llm_workers_activity_failures_total{error_type="InferenceUnavailable"}` rising); sections end `failed`.

**Check and do.**
1. Each activity retries (3 attempts, backing off). A short outage is absorbed.
2. A long one fails sections. When most sections of a document fail with one error, the poison breaker stops the document (`poison-section-breaker.md`) instead of spending retries.
3. Foundation-section failures fail the run with the section's own reason; other sections leave the run `complete` with those sections `failed`.
4. After the Gateway is back, affected runs are regenerated through the API (sections that failed are rewritten; finished ones are kept).

**Do not.** The workers never call a model provider directly. Do not point them at one.
