"""``ExtractionWorkflow`` (LLD §6.3): one short activity inside a workflow (W-6).

Flow: ``run_state`` -> ``begin_stage(extract)`` -> ``run_extraction`` -> done. A quota refusal
suspends the run and the same step repeats on resume. A residency or schema failure fails the
run. A restart (W-8) continues from the row. There is no outline, gate or fan-out.

Versioning: pinned (WV-6), it lives for minutes. Security: identifiers only.
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from llm_models.workers import WORKFLOW_EXTRACTION, GenerationWorkflowInput, RunResult

    from llm_workers_generation.types import ExtractionStep, RunRequest, StageRequest, StepResult
    from llm_workers_generation.workflows._common import (
        WORK,
        WRITE,
        End,
        Fail,
        RunWorkflowBase,
    )


@workflow.defn(name=WORKFLOW_EXTRACTION, versioning_behavior=VersioningBehavior.PINNED)
class ExtractionWorkflow(RunWorkflowBase):
    """Extraction out of the synchronous budget."""

    @workflow.run
    async def run(self, raw: dict[str, Any]) -> RunResult:
        """Run once; the result is small and callers read the tables."""
        GenerationWorkflowInput.from_dict(raw)  # a malformed input fails the task, not the run
        self._begin(raw, "extraction")
        self._stage = "extract"
        try:
            result = await self._extract()
        except End as end:
            result = end.result
        except Fail as fail:
            result = await self._write_failure(fail)
        return self._finish(result)

    async def _extract(self) -> RunResult:
        state = await self._state()
        if state.status == "suspended":
            await self._wait_suspended()
        while True:
            self._check_cancel()
            begun = await self._call(
                "begin_stage", StageRequest(self._raw, "extract"), StepResult, WRITE
            )
            if begun.gone:
                raise End(RunResult("gone"))
            if not begun.ok:
                await self._state()  # terminal: ends unwritten (WB-6)
            step = await self._call("run_extraction", RunRequest(self._raw), ExtractionStep, WORK)
            if step.gone:
                raise End(RunResult("gone"))
            if step.skipped_terminal:
                await self._state()
                raise End(RunResult("failed", "internal"))
            if step.quota_suspended:
                await self._suspend("extraction")
                continue  # the same step again, from the row (§9.1)
            return RunResult("complete")
