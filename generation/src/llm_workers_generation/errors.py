"""Error type codes and the fixed, content-free messages (LLD §4.4, WS-11). Standard library only.

An engine exception can carry text and Temporal writes an activity failure to history, which purge
and erasure do not reach. So every failure that leaves an activity is
``ApplicationError(type=<code>, message=<fixed text>)``: no details, no chaining.

Replay safety: pure data. Security: the message table is the only text that can enter history.
"""

from __future__ import annotations

ERR_SCOPE_REVOKED = "ScopeRevoked"
ERR_RESIDENCY = "ResidencyDenied"
ERR_SCHEMA = "SchemaViolation"
ERR_NOTHING_TO_MINT = "NothingToMint"
ERR_INFERENCE = "InferenceUnavailable"
ERR_STORE = "StoreUnwritable"
ERR_INTERNAL = "Internal"
ERR_PLAN_REFUSED = "PlanRefused"

NON_RETRYABLE = (
    ERR_SCOPE_REVOKED,
    ERR_RESIDENCY,
    ERR_SCHEMA,
    ERR_NOTHING_TO_MINT,
    ERR_PLAN_REFUSED,
)

FIXED_MESSAGES: dict[str, str] = {
    ERR_SCOPE_REVOKED: "scope revoked",
    ERR_RESIDENCY: "residency refused",
    ERR_SCHEMA: "schema violation",
    ERR_NOTHING_TO_MINT: "nothing to mint",
    ERR_INFERENCE: "inference unavailable",
    ERR_STORE: "store unwritable",
    ERR_INTERNAL: "internal error",
    ERR_PLAN_REFUSED: "plan refused",
}

# error type -> the run failure reason fail_run writes (§4.4)
REASON_FOR_ERROR: dict[str, str] = {
    ERR_SCOPE_REVOKED: "scope_revoked",
    ERR_RESIDENCY: "residency_capacity",
    ERR_SCHEMA: "schema_violation",
    ERR_NOTHING_TO_MINT: "internal",
    ERR_INFERENCE: "inference_unavailable",
    ERR_STORE: "artefact_store_unwritable",
    ERR_INTERNAL: "internal",
}


def reason_for(error_type: str) -> str:
    """The failure reason for an error type; anything unknown is ``internal``."""
    return REASON_FOR_ERROR.get(error_type, "internal")
