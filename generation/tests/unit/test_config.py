import pytest
from llm_workers_generation.config import Settings
from pydantic import ValidationError


def make(**kw: object) -> Settings:
    return Settings(worker_kind="generation", worker_tier="standard", **kw)  # type: ignore[arg-type]


def test_queue_and_deployment_are_derived_not_configured() -> None:
    s = make()
    assert s.work_queue == "llm-generation" and s.control_queue == "llm-generation-control"
    assert s.deployment_name == "llm-generation"
    assert (
        Settings(worker_kind="extraction", worker_tier="strict").work_queue
        == "llm-extraction-strict"
    )


def test_namespace_required_outside_local() -> None:
    with pytest.raises(ValidationError):
        make(app_env="production")
    make(
        app_env="production",
        temporal_namespace="llm-generation-prod",
        payload_codec_key_id="k",
        payload_codec_key_path="/keys/k",
    )


def test_tls_files_come_in_pairs() -> None:
    with pytest.raises(ValidationError):
        make(temporal_tls_cert_path="/c")


def test_codec_key_required_outside_local() -> None:
    with pytest.raises(ValidationError):
        make(app_env="staging", temporal_namespace="x")


def test_fleet_cap() -> None:
    s = make(max_replicas=10, max_concurrent_activities=8, fanout_max_global=100)
    assert s.fleet_cap == 80 and s.fleet_cap_disagrees
    assert not make(
        max_replicas=25, max_concurrent_activities=4, fanout_max_global=100
    ).fleet_cap_disagrees


def test_global_bound_cannot_be_below_tenant_bound() -> None:
    with pytest.raises(ValidationError):
        make(fanout_max_global=5)


def test_shutdown_grace_cannot_be_shorter_than_an_activity() -> None:
    with pytest.raises(ValidationError):
        make(worker_shutdown_grace_seconds=10)
