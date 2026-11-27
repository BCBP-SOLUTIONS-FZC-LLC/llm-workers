"""The slot activities never let a client error (it names the workflow id, so a tenant id) leave."""

import pytest
from llm_workers_generation.activities.slots import SlotActivities
from llm_workers_generation.types import SlotAcquire
from temporalio.exceptions import ApplicationError

TENANT = "tenant-secret-1"


class _Broken:
    async def start_update_with_start_workflow(self, *a: object, **k: object) -> None:
        raise RuntimeError(f"update on slots:llm-generation:{TENANT} failed")


async def test_a_client_error_leaves_as_a_fixed_retryable_error() -> None:
    req = SlotAcquire({"tenant_id": TENANT}, "llm-generation", "req-1")
    with pytest.raises(ApplicationError) as info:
        await SlotActivities(_Broken()).acquire_slot(req)  # type: ignore[arg-type]
    err = info.value
    assert err.type == "Internal" and not err.non_retryable
    assert TENANT not in str(err) and err.__cause__ is None  # no chained cause reaches history


@pytest.mark.parametrize("tenant", ["", "a b", "x" * 65, "../etc", None])
async def test_an_odd_tenant_id_is_refused_not_pooled_into_a_shared_bucket(tenant: object) -> None:
    req = SlotAcquire({"tenant_id": tenant}, "llm-generation", "req-1")
    for call in (SlotActivities(_Broken()).acquire_slot, SlotActivities(_Broken()).release_slot):
        with pytest.raises(ApplicationError) as info:
            await call(req)  # type: ignore[arg-type]
        assert info.value.type == "Internal" and info.value.non_retryable
