"""The section graph: a LangGraph state machine for one section (LLD §7.3, WA-9..13, WA-18).

    reresolve -> retrieve -> [sufficient?] -> (retrieve deeper) -> refine -> compose -> draft
              -> verify -> [weak and retries left?] -> compose ... | save      or      save_gap

It runs to completion **inside one activity** (WA-9) and installs **no LangGraph checkpointer**
(WA-18): a stock checkpointer stores the whole state, text included. Content (chunks, prompts,
drafts) lives inside the ``SectionSteps`` object in process memory and never enters graph state,
which carries control integers and flags only. Node names, timings and counts are collected in a
``trace`` and written once by the activity through ``record_checkpoint`` (WA-11, GC-1).

Hexagonal: the graph knows ``SectionSteps`` (a port) and nothing of the engine or Temporal.
Replay safety: not applicable, this is activity code. Security: no content in state or trace.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from llm_models.workers import SectionOutcome

from llm_workers_generation import tuning

Trace = list[tuple[str, int, int]]
"""``(node, milliseconds, count)`` per executed node. Names, timings and counts only."""


class SectionSteps(Protocol):
    """What the graph needs. Content stays inside the implementation."""

    async def reresolve(self) -> None:
        """Re-read access and re-resolve scope (``WP-4``). Raises where nothing is left."""

    async def retrieve(self, *, deeper: bool) -> bool:
        """Search; ``deeper`` widens it. Returns whether the evidence is sufficient."""

    def insufficient_reason(self) -> str:
        """A content-free reason for a gap."""

    async def refine(self) -> None:
        """The Instruction Refiner, for a regeneration only (flag-gated, a no-op otherwise)."""

    async def compose(self, attempt: int) -> None:
        """Build the six-layer prompt."""

    async def draft(self, attempt: int) -> None:
        """Draft through the Gateway."""

    async def verify(self, attempt: int) -> bool:
        """The verification ladder. Returns whether the result asks for a retry."""

    async def save(self, retries: int) -> SectionOutcome:
        """Write the version through the engine."""

    async def save_gap(self, reason: str) -> SectionOutcome:
        """End the section as a gap through the engine."""


class GraphState(TypedDict):
    """Control state only: never text."""

    retrieves: int
    sufficient: bool
    attempt: int
    weak: bool
    outcome: SectionOutcome | None


def initial_state() -> GraphState:
    """The state a run starts from."""
    return {"retrieves": 0, "sufficient": False, "attempt": 1, "weak": False, "outcome": None}


def build_section_graph(steps: SectionSteps, trace: Trace) -> Any:
    """Compile the graph for one activity. No checkpointer is passed, deliberately (WA-18)."""

    def node(
        name: str, fn: Callable[[GraphState], Awaitable[dict[str, Any]]]
    ) -> Callable[[GraphState], Awaitable[Any]]:
        async def run(state: GraphState) -> Any:
            started = time.perf_counter()
            update = await fn(state)
            trace.append((name, int((time.perf_counter() - started) * 1000), state["attempt"]))
            return update

        return run

    async def reresolve(_: GraphState) -> dict[str, Any]:
        await steps.reresolve()
        return {}

    async def retrieve(s: GraphState) -> dict[str, Any]:
        return {"retrieves": s["retrieves"] + 1, "sufficient": await steps.retrieve(deeper=False)}

    async def retrieve_deeper(s: GraphState) -> dict[str, Any]:
        return {"retrieves": s["retrieves"] + 1, "sufficient": await steps.retrieve(deeper=True)}

    async def refine(_: GraphState) -> dict[str, Any]:
        await steps.refine()
        return {}

    async def compose(s: GraphState) -> dict[str, Any]:
        await steps.compose(s["attempt"])
        return {}

    async def draft(s: GraphState) -> dict[str, Any]:
        await steps.draft(s["attempt"])
        return {}

    async def verify(s: GraphState) -> dict[str, Any]:
        weak = await steps.verify(s["attempt"])
        retry = weak and s["attempt"] <= tuning.SECTION_MAX_AUTO_RETRIES  # ASV-2: a ceiling of two
        return {"weak": retry, "attempt": s["attempt"] + 1 if retry else s["attempt"]}

    async def save(s: GraphState) -> dict[str, Any]:
        return {"outcome": await steps.save(s["attempt"] - 1)}

    async def gap(_: GraphState) -> dict[str, Any]:
        return {"outcome": await steps.save_gap(steps.insufficient_reason())}

    g = StateGraph(GraphState)
    add_node: Any = g.add_node  # LangGraph's overloads do not model a partial-update node
    for name, fn in (
        ("reresolve", reresolve),
        ("retrieve", retrieve),
        ("retrieve_deeper", retrieve_deeper),
        ("refine", refine),
        ("compose", compose),
        ("draft", draft),
        ("verify", verify),
        ("save", save),
        ("gap", gap),
    ):
        add_node(name, node(name, fn))
    g.add_edge(START, "reresolve")
    g.add_edge("reresolve", "retrieve")
    g.add_conditional_edges(
        "retrieve",
        lambda s: "refine" if s["sufficient"] else "retrieve_deeper",
        {"refine": "refine", "retrieve_deeper": "retrieve_deeper"},
    )
    g.add_conditional_edges(
        "retrieve_deeper",
        lambda s: "refine" if s["sufficient"] else "gap",
        {"refine": "refine", "gap": "gap"},
    )
    g.add_edge("refine", "compose")
    g.add_edge("compose", "draft")
    g.add_edge("draft", "verify")
    g.add_conditional_edges(
        "verify",
        lambda s: "compose" if s["weak"] else "save",
        {"compose": "compose", "save": "save"},
    )
    g.add_edge("save", END)
    g.add_edge("gap", END)
    return g.compile()  # no checkpointer


async def run_section_graph(steps: SectionSteps, trace: Trace) -> SectionOutcome:
    """Run to completion and return the outcome the engine wrote."""
    final: GraphState = await build_section_graph(steps, trace).ainvoke(initial_state())
    assert final["outcome"] is not None  # noqa: S101 - every path ends in save or gap
    return final["outcome"]
