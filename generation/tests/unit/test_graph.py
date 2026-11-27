"""The section graph (LLD §7.3, WA-9..13, WA-18)."""

from llm_models.workers import SectionOutcome
from llm_workers_generation.graphs.section_graph import (
    build_section_graph,
    initial_state,
    run_section_graph,
)


class Steps:
    def __init__(self, sufficient=(True,), weak=(False,)) -> None:  # noqa: ANN001
        self.sufficient, self.weak, self.calls = list(sufficient), list(weak), []
        self.text = "CANARY drafted text"  # content lives on the steps object, never in graph state

    async def reresolve(self) -> None:
        self.calls.append("reresolve")

    async def retrieve(self, *, deeper: bool) -> bool:
        self.calls.append("retrieve_deeper" if deeper else "retrieve")
        return self.sufficient.pop(0)

    def insufficient_reason(self) -> str:
        return "no_evidence"

    async def refine(self) -> None:
        self.calls.append("refine")

    async def compose(self, attempt: int) -> None:
        self.calls.append(f"compose{attempt}")

    async def draft(self, attempt: int) -> None:
        self.calls.append(f"draft{attempt}")

    async def verify(self, attempt: int) -> bool:
        self.calls.append(f"verify{attempt}")
        return self.weak.pop(0)

    async def save(self, retries: int) -> SectionOutcome:
        self.calls.append(f"save{retries}")
        return SectionOutcome("s", "complete")

    async def save_gap(self, reason: str) -> SectionOutcome:
        self.calls.append(f"gap:{reason}")
        return SectionOutcome("s", "gap", gap_reason=reason)


async def test_happy_path_order() -> None:
    steps, trace = Steps(), []
    out = await run_section_graph(steps, trace)
    assert out.state == "complete"
    assert steps.calls == [
        "reresolve",
        "retrieve",
        "refine",
        "compose1",
        "draft1",
        "verify1",
        "save0",
    ]
    assert [n for n, _, _ in trace] == [
        "reresolve",
        "retrieve",
        "refine",
        "compose",
        "draft",
        "verify",
        "save",
    ]


async def test_insufficient_once_retrieves_deeper_then_drafts() -> None:
    steps = Steps(sufficient=(False, True))
    out = await run_section_graph(steps, [])
    assert out.state == "complete" and "retrieve_deeper" in steps.calls


async def test_insufficient_twice_is_a_gap_and_never_drafts() -> None:
    steps = Steps(sufficient=(False, False))
    out = await run_section_graph(steps, [])
    assert out.state == "gap" and out.gap_reason == "no_evidence"
    assert not any(c.startswith("draft") for c in steps.calls)


async def test_weak_verification_retries_with_ceiling_of_two() -> None:
    steps = Steps(weak=(True, True, True))  # always weak
    out = await run_section_graph(steps, [])
    assert out.state == "complete"
    assert [c for c in steps.calls if c.startswith("draft")] == ["draft1", "draft2", "draft3"]
    assert steps.calls[-1] == "save2"  # saved after the retries are used (ASV-2)


async def test_one_weak_then_pass() -> None:
    steps = Steps(weak=(True, False))
    await run_section_graph(steps, [])
    assert steps.calls[-1] == "save1"


async def test_graph_has_no_checkpointer_and_state_carries_no_text() -> None:
    steps, trace = Steps(), []
    graph = build_section_graph(steps, trace)
    assert graph.checkpointer is None  # WA-18
    final = await graph.ainvoke(initial_state())
    assert set(final) == {"retrieves", "sufficient", "attempt", "weak", "outcome"}
    assert "CANARY" not in repr(final) and "CANARY" not in repr(trace)  # WA-11
    assert all(
        isinstance(n, str) and isinstance(ms, int) and isinstance(c, int) for n, ms, c in trace
    )
