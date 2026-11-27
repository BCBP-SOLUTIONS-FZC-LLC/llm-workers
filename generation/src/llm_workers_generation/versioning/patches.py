"""The patch-marker registry and the retention rule (LLD §5.5, WV-1..WV-4).

Any change to the order or number of commands in a workflow uses ``workflow.patched("<id>")`` so
old histories replay unchanged. **No workflow change has shipped yet, so no marker exists**: this
is revision one of every workflow. When the first change ships, add its id here with the date, use
the id in the workflow with ``workflow.patched``, and remove both only when ``retention_days``
have passed since the build that introduced it was fully rolled out (WV-4).
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_workers_generation import tuning


@dataclass(frozen=True, slots=True)
class Patch:
    """One marker in use."""

    id: str
    workflow: str
    introduced: str
    """ISO date of the build that first emitted it."""
    reason: str


PATCHES: tuple[Patch, ...] = ()


def retention_days() -> int:
    """A marker stays until no older run can be open (WV-4): gate + suspension + run."""
    return tuning.REVIEW_GATE_DAYS + tuning.SUSPEND_MAX_DAYS + -(-tuning.RUN_MAX_SECONDS // 86_400)
