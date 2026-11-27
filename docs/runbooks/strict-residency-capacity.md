# A strict-residency capacity shortage

**What you see.** Strict-tier runs fail or wait with `residency_capacity`: no capacity in the tenant's region.

**Check and do.**
1. The `strict` queues (`llm-generation-strict`, `llm-extraction-strict`) serve tenants with a residency requirement; one strict queue per kind exists until regions are named.
2. A residency refusal is terminal for that run (`residency_capacity`): the Gateway has no region capacity. Raising capacity is the Gateway's.
3. After capacity returns, regenerate through the API.

**Do not.** Never route a strict tenant's work to a standard queue to get it done.
