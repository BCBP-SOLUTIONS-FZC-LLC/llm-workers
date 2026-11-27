"""Process settings, validated at start-up (LLD §16).

Only values no workflow reads live here (a workflow reads ``tuning``, ADR-0004). Durations are in
seconds and carry the unit in their name. The process fails fast on a bad combination.

Security: the codec key path is a ``SecretStr`` and never logged. There is no database, pool or
event setting here (rules 2 and 3 of CLAUDE.md): ``rag.engine.build_engine`` builds its pools from
its own settings, in the process environment.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from llm_workers_generation import tuning
from llm_workers_generation.queues import Kind, Tier, control_queue, queue_name

_LOCAL_ENVS = frozenset({"local", "dev", "test"})


class Settings(BaseSettings):
    """Environment-backed settings of one worker process (one Deployment per queue, §3.4)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_env: str = "local"
    temporal_address: str = Field(default="localhost:7233")
    temporal_namespace: str | None = None
    temporal_tls_cert_path: str | None = None
    temporal_tls_key_path: str | None = None
    worker_kind: Kind
    worker_tier: Tier
    build_id: str = "dev"
    max_concurrent_activities: int = Field(default=8, ge=1)
    max_concurrent_control_activities: int = Field(default=16, ge=1)
    max_concurrent_workflow_tasks: int = Field(default=32, ge=1)
    fanout_max_global: int = Field(default=tuning.FANOUT_MAX_GLOBAL, ge=1)
    max_replicas: int = Field(default=12, ge=1)
    gateway_shed_threshold_percent: int = Field(default=25, ge=1, le=100)
    gateway_shed_window_seconds: int = Field(default=300, ge=1)
    worker_shutdown_grace_seconds: int = Field(default=tuning.ACTIVITY_START_TO_CLOSE_SECONDS, ge=1)
    payload_codec_key_id: str | None = None
    payload_codec_key_path: SecretStr | None = None
    enable_metrics: bool = True
    observability_domain: str = "llm"
    metrics_bind_address: str = "0.0.0.0:9464"  # noqa: S104 - scraped inside the namespace only
    health_port: int = 8080
    otel_exporter_otlp_endpoint: str | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def work_queue(self) -> str:
        """The queue this process polls, derived from kind and tier (§16: never configured)."""
        return queue_name(self.worker_kind, self.worker_tier)

    @property
    def control_queue(self) -> str:
        """The control queue the same pod also polls (§12.3)."""
        return control_queue(self.work_queue)

    @property
    def deployment_name(self) -> str:
        """The Worker Deployment is named for the queue (WV-5)."""
        return self.work_queue

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if tuning.ACTIVITY_START_TO_CLOSE_SECONDS <= 2 * tuning.ACTIVITY_HEARTBEAT_SECONDS:
            raise ValueError("start-to-close must exceed twice the heartbeat")
        if self.worker_shutdown_grace_seconds < tuning.ACTIVITY_START_TO_CLOSE_SECONDS:
            raise ValueError("shutdown grace must be at least the activity start-to-close")
        if tuning.TENANT_SLOT_LEASE_SECONDS < (
            tuning.ACTIVITY_MAX_ATTEMPTS * tuning.ACTIVITY_START_TO_CLOSE_SECONDS
        ):
            raise ValueError("slot lease must cover every attempt of a section")
        if tuning.FANOUT_MAX_PER_TENANT > self.fanout_max_global:
            raise ValueError("per-tenant fan-out must not exceed the global bound")
        if tuning.FANOUT_MAX_PER_DOCUMENT > tuning.FANOUT_MAX_PER_TENANT:
            raise ValueError("per-document fan-out must not exceed the per-tenant bound")
        if not 1 <= tuning.POISON_THRESHOLD_PERCENT <= 100:
            raise ValueError("poison threshold must be a percentage")
        if tuning.WAIT_RECONCILE_SECONDS > 86_400:
            raise ValueError("reconcile interval must not exceed one day")
        if self.app_env not in _LOCAL_ENVS and not self.temporal_namespace:
            raise ValueError("TEMPORAL_NAMESPACE is required outside local, dev and test")
        if (self.temporal_tls_cert_path is None) != (self.temporal_tls_key_path is None):
            raise ValueError("TEMPORAL_TLS_CERT_PATH and TEMPORAL_TLS_KEY_PATH are set together")
        if self.app_env not in _LOCAL_ENVS and not (
            self.payload_codec_key_id and self.payload_codec_key_path
        ):
            raise ValueError("the payload codec key is required outside local, dev and test")
        return self

    @property
    def fleet_cap(self) -> int:
        """Pods times concurrent activities: the hard ceiling on running sections (§12.2)."""
        return self.max_replicas * self.max_concurrent_activities

    @property
    def fleet_cap_disagrees(self) -> bool:
        """Start-up logs a warning where ``FANOUT_MAX_GLOBAL`` and the fleet cap differ."""
        return self.fleet_cap != self.fanout_max_global
