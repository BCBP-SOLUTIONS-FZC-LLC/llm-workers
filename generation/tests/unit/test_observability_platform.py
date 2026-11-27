"""Logs and traces go through platform-fastapicommon (rule 1), including the stdlib ``temporalio`` loggers."""

import json
import logging

import pytest
from fastapicommon.telemetry import active_tracing_config, shutdown_tracing
from llm_workers_generation.config import Settings
from llm_workers_generation.observability.setup import (
    build_interceptors,
    init_observability,
    shutdown_observability,
)
from prometheus_client import CollectorRegistry


def settings(**kw: object) -> Settings:
    return Settings(worker_kind="generation", worker_tier="standard", **kw)  # type: ignore[arg-type]


PROD = settings(
    app_env="prod",
    temporal_namespace="ns",
    payload_codec_key_id="k",
    payload_codec_key_path="/k",
)


@pytest.fixture(autouse=True)
def _reset_tracing():  # noqa: ANN202
    yield
    shutdown_tracing()


def test_platform_logging_formats_the_workers_own_log_as_json_in_production(capfd) -> None:  # noqa: ANN001
    from llm_workers_generation.worker import log

    init_observability(PROD, registry=CollectorRegistry())
    log.warning("fanout_global_disagrees_with_fleet_cap", fleet_cap=96)
    line = json.loads(capfd.readouterr().err.strip().splitlines()[-1])
    assert line["msg"] == "fanout_global_disagrees_with_fleet_cap" and line["fleet_cap"] == 96
    assert line["level"] == "warn" and "caller" in line


def test_stdlib_temporalio_loggers_come_out_in_the_same_format(capfd) -> None:  # noqa: ANN001
    """The SDK, activity and workflow loggers are stdlib loggers: the platform root handler renders them."""
    init_observability(PROD, registry=CollectorRegistry())
    logging.getLogger("temporalio.worker").warning("sdk says hello")
    line = json.loads(capfd.readouterr().err.strip().splitlines()[-1])
    assert line["msg"] == "sdk says hello" and line["level"] == "warn"


def test_tracer_provider_is_installed_by_fastapicommon_only_where_an_endpoint_is_set() -> None:
    init_observability(settings(), registry=CollectorRegistry())
    assert active_tracing_config() is None and build_interceptors(settings()) == []
    s = settings(otel_exporter_otlp_endpoint="none", build_id="b1", observability_domain="llm")
    init_observability(
        s, registry=CollectorRegistry()
    )  # "none" keeps spans in process: no exporter in a test
    cfg = active_tracing_config()
    assert cfg is not None and cfg.service_name == "llm-workers" and cfg.service_version == "b1"
    assert cfg.domain == "llm" and len(build_interceptors(s)) == 1
    shutdown_observability()
    assert active_tracing_config() is None


def test_metrics_identity_is_checked_at_start_and_an_unknown_environment_fails_fast() -> None:
    """fastapicommon's vocabulary is local, dev, test, staging, prod: ``production`` is refused."""
    bad = settings(
        app_env="production",
        temporal_namespace="ns",
        payload_codec_key_id="k",
        payload_codec_key_path="/k",
    )
    with pytest.raises(ValueError, match="environment 'production'"):
        init_observability(bad, registry=CollectorRegistry())
    with pytest.raises(ValueError, match="domain"):
        init_observability(settings(observability_domain="nope"), registry=CollectorRegistry())


def test_the_worker_identity_gives_the_llm_workers_prefix_and_blocks_engine_metrics(
    app_registry,
) -> None:  # noqa: ANN001
    """One process, one metrics identity. With ``llm-workers`` the engine's own Tier 3 names
    (``llm_retrieval_generation_*``) raise ValueError, which is why ``build_rag_engine`` turns the engine's
    metrics off in a worker (ADR-0006)."""
    from fastapicommon.metrics import new_counter

    new_counter("llm_workers_probe_total", "x", registry=app_registry)
    with pytest.raises(ValueError, match="llm_workers_"):
        new_counter("llm_retrieval_generation_probe_total", "x", registry=app_registry)


def test_metrics_disabled_initialises_nothing_and_still_logs(capfd) -> None:  # noqa: ANN001
    from llm_workers_generation.observability.app_metrics import app_metrics

    off = settings(enable_metrics=False)
    init_observability(off, registry=CollectorRegistry())
    # no identity check at all, so even an unknown environment is accepted when metrics are off
    init_observability(
        settings(
            enable_metrics=False,
            app_env="whatever",
            temporal_namespace="n",
            payload_codec_key_id="k",
            payload_codec_key_path="/k",
        )
    )
    assert app_metrics() is not None


def test_the_environment_label_is_the_same_on_both_endpoints() -> None:
    """``init_metrics`` normalises ``APP_ENV``; the Temporal runtime's tags must too (naming rule 8)."""
    from llm_workers_generation.observability.setup import platform_labels

    assert (
        platform_labels(
            settings(
                app_env=" Prod ",
                temporal_namespace="ns",
                payload_codec_key_id="k",
                payload_codec_key_path="/k",
            )
        )["environment"]
        == "prod"
    )
    assert platform_labels(settings(app_env="test")) == {
        "domain": "llm",
        "service": "llm-workers",
        "environment": "test",
    }
    with pytest.raises(ValueError, match="environment 'production'"):
        platform_labels(
            settings(
                app_env="production",
                temporal_namespace="ns",
                payload_codec_key_id="k",
                payload_codec_key_path="/k",
            )
        )
