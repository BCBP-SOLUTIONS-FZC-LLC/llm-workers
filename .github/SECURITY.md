# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| *(none released yet)* | Nothing is deployed. Once a `v1.x` ships, the latest 1.x is the supported line |

Older major versions are not patched. Deploy the latest release image.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email: vijay@bcbpsolutions.com
Subject: `[llm-workers] Security vulnerability`

Include in your report:
- Description of the vulnerability and the affected component (workflow, activity, payload codec, task queue, deployment)
- Steps to reproduce
- Potential impact (content in Temporal history, tenant data leakage, scope bypass, privilege escalation, DoS, etc.)
- Suggested fix or patch (if any)

### Response timeline

| Step | Target |
|------|--------|
| Initial acknowledgement | 48 hours |
| Severity assessment | 5 business days |
| Patch release (critical/high) | 14 days |
| Public disclosure | After patch ships |

We follow responsible disclosure. Reporters will be credited in release notes unless anonymity is requested.

## Scope

Areas of particular sensitivity in this service:

- **Content in Temporal history** — history sits in a namespace on a cluster this service does not operate, where purge and erasure do not reach. Workflow input, signals, activity results, failures and heartbeats carry identifiers and counts only. A leak of prompt, chunk, generated text or a reviewer's instruction into history is a vulnerability (LLD WS-6, WS-9..WS-11).
- **Payload codec and failure converter** — AES-GCM keys and the encoded failure attributes (`codec/`); a weakened cipher, a wrong key id or a plain-text failure would make an exported history readable.
- **Tenant isolation through the engine** — every row access goes through `rag.engine` under the activity role with row-level security; a path that reads or writes around it, or a scope taken from the run input instead of being re-resolved, would expose cross-tenant data (WS-1..WS-3).
- **Queue routing** — a strict-residency run must never run on a standard or enterprise queue (WQ-2).
- **Worker identity to Temporal** — the client certificate and key mounted from files; who may start, signal and poll in the namespace.
- **Supply chain** — the engine is a private git dependency pinned in `uv.lock`; the image is built from a locked set and scanned before release.
