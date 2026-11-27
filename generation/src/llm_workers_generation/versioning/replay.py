"""Replay every recorded history against the current workflow code (LLD §18.2, WV-3).

``replay_directory`` loads ``*.json`` histories (``WorkflowHistory.to_json``) from a directory
and replays each; a history that no longer replays raises, which fails CI. A nightly job exports a
sample of live histories (identifiers only, decoded through the codec with a read-only key) into
the same shape.
"""

from __future__ import annotations

from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from llm_workers_generation.workflows import WORKFLOWS


def build_replayer() -> Replayer:
    """A replayer over every workflow type this build ships."""
    return Replayer(workflows=WORKFLOWS)


async def replay_histories(histories: list[WorkflowHistory]) -> None:
    """Replay each history; raises ``NondeterminismError`` on the first break."""
    replayer = build_replayer()
    for history in histories:
        await replayer.replay_workflow(history)


async def replay_directory(directory: Path) -> int:
    """Replay every ``*.json`` history in ``directory``. Returns how many were replayed."""
    files = sorted(directory.glob("*.json"))  # noqa: ASYNC240 - a test-time directory read
    await replay_histories([WorkflowHistory.from_json(f.stem, f.read_text()) for f in files])
    return len(files)
