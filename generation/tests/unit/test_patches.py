from llm_workers_generation.versioning.deployment import deployment_config
from llm_workers_generation.versioning.patches import PATCHES, retention_days


def test_retention_covers_gate_suspension_and_run() -> None:
    assert retention_days() >= 60  # WV-4: gate 30 + suspension 30 + the run deadline, rounded up


def test_no_patch_has_shipped_yet() -> None:
    assert PATCHES == ()


def test_deployment_is_named_for_the_queue_and_pinned_by_default() -> None:
    cfg = deployment_config("llm-generation", "img-1")
    assert cfg.version.deployment_name == "llm-generation" and cfg.version.build_id == "img-1"
    assert cfg.use_worker_versioning
    assert cfg.default_versioning_behavior.name == "PINNED"


def test_workflow_versioning_behaviours() -> None:
    from llm_workers_generation.workflows import (
        ExtractionWorkflow,
        GenerationWorkflow,
        SectionWorkflow,
        TenantSlots,
    )
    from temporalio import workflow
    from temporalio.common import VersioningBehavior

    def behaviour(cls: type) -> VersioningBehavior:
        return workflow._Definition.must_from_class(cls).versioning_behavior  # noqa: SLF001

    assert behaviour(GenerationWorkflow) is VersioningBehavior.AUTO_UPGRADE
    assert behaviour(TenantSlots) is VersioningBehavior.AUTO_UPGRADE
    assert behaviour(SectionWorkflow) is VersioningBehavior.PINNED
    assert behaviour(ExtractionWorkflow) is VersioningBehavior.PINNED
