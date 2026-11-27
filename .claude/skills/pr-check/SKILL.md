---
name: pr-check
description: Run the full pre-PR gate set for llm-workers and report pass/fail per gate.
disable-model-invocation: true
---
Run from the repo root and report each gate's result. Stop and show the output of the first real failure.
Needs: network for `vuln-check` and for the Temporal test server download (first test run); an ssh key that can read the engine repo for `make setup`. If one is unavailable, report that gate as `skipped (offline)` or `skipped (no engine access)`, run the rest, and say the verdict is partial.
1. `make lock-check fmt-check lint typecheck vuln-check`
2. `make arch replay` (architecture rules, engine contract, recorded histories replay)
3. `make cover-check` (the whole suite, 95% coverage gate)
4. If the diff touches `Dockerfile` or the engine pin: `make trivy` (needs Docker and GO_PRIVATE_TOKEN or `gh auth login`).
Do not edit `generation/tests/contract/engine_api_v1.json` to make a contract test pass: a moved engine shape needs a deliberate pin bump. Finish with a short verdict: ready for a PR, or what blocks it.
