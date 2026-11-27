"""Collect the activities a worker registers (LLD §3.4, §7.2).

Work-queue activities and control-queue activities are separate lists: the same pod runs a work
worker (workflows plus long activities) and a second, activity-only control worker with its own
slots, so a run-state write never queues behind ten-minute sections (§12.3, LW-16).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from temporalio.client import Client

from llm_workers_generation.activities.generation import GenerationActivities
from llm_workers_generation.activities.ports import EngineApi
from llm_workers_generation.activities.slots import SlotActivities


def build_activities(
    api: EngineApi, client: Client
) -> tuple[list[Callable[..., Any]], list[Callable[..., Any]]]:
    """``(work_activities, control_activities)``."""
    g, s = GenerationActivities(api), SlotActivities(client)
    work: list[Callable[..., Any]] = [
        g.outline,
        g.write_section,
        g.capture_facts,
        g.coherence_pass,
        g.run_extraction,
    ]
    control: list[Callable[..., Any]] = [
        g.run_state,
        g.check_scope,
        g.begin_stage,
        g.suspend_for_quota,
        g.complete_run,
        g.fail_run,
        g.fail_section,
        g.mint_document_version,
        s.acquire_slot,
        s.release_slot,
    ]
    return work, control
