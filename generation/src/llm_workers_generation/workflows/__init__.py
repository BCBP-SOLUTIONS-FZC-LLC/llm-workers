"""Workflow definitions. Plain, deterministic code: no I/O (LLD §5.4, WD-1, WDT-1..5)."""

from llm_workers_generation.workflows.extraction_workflow import ExtractionWorkflow
from llm_workers_generation.workflows.generation_workflow import GenerationWorkflow
from llm_workers_generation.workflows.section_workflow import SectionWorkflow
from llm_workers_generation.workflows.tenant_slots_workflow import TenantSlots

WORKFLOWS = [GenerationWorkflow, SectionWorkflow, ExtractionWorkflow, TenantSlots]

__all__ = [
    "WORKFLOWS",
    "ExtractionWorkflow",
    "GenerationWorkflow",
    "SectionWorkflow",
    "TenantSlots",
]
