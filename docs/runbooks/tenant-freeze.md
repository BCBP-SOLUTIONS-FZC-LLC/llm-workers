# A tenant freeze

**What you see.** A tenant is frozen (suspended) while its runs are live.

**Check and do.**
1. Decided (H-4): a freeze should pause long runs until the tenant is reactivated, like quota. **This is not built yet** (owed, slice 7): today a frozen tenant's runs end through scope re-resolution (`scope_revoked`) if access disappears.
2. Until it is built, ask the API team before freezing a tenant that has runs in flight.

**Do not.** Do not rely on a freeze pausing runs.
