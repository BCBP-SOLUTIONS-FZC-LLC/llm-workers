"""The section graph end to end (LLD §7.3): ``SectionWorkflow`` -> ``write_section`` -> the real
LangGraph graph -> the engine port, with a fake model.

Real here: the Temporal workflow and activities, the ``SectionSteps`` bridge and the compiled LangGraph
graph (no checkpointer). Fake: the engine behind the port, including the model (``call_model``) and
the verifier. Time-skipping server: nothing here cancels a child while an activity retries.
"""

from typing import Any

from fake_engine import FakeDb, make_input
from harness import stack
from llm_models.workers import SectionJob, SectionOutcome

CANARY = (
    "CANARY"  # the fake engine puts this in every text it returns: it must reach no checkpoint row
)


def section_db(**behaviors: list[str]) -> FakeDb:
    db = FakeDb(
        status="generating",
        stage="stage2",
        outline_state="approved",
        outline_version=1,
        behaviors=dict(behaviors),
    )
    db.add_sections([("1", "intro", True), ("2", "scope", False)])
    return db


async def run_section(s: Any, db: FakeDb, ref: str = "2") -> Any:
    section = next(x for x in db.sections if x["ref"] == ref)
    return await s.client.execute_workflow(
        "SectionWorkflow",
        SectionJob(section["id"], "stage2", make_input(outline_gate=False)),
        id=f"graph-{ref}",
        task_queue="llm-generation",
        result_type=SectionOutcome,
    )


async def test_the_graph_runs_every_node_and_reaches_save_section() -> None:
    db = section_db()
    async with stack(db) as s:
        outcome = await run_section(s, db)
    assert outcome.state == "complete" and outcome.failure_reason is None
    assert db.count("save_section") == 1 and db.count("fail_section") == 0
    assert db.count("call_model:2") == 1  # one draft
    assert [n for n, _, _ in db.checkpoints] == [
        "reresolve",
        "retrieve",
        "refine",
        "compose",
        "draft",
        "verify",
        "save",
    ]  # the names, timings and attempt numbers the checkpoint row holds
    assert CANARY not in repr(db.checkpoints)  # no text in the inspection row (WA-18)
    assert next(x for x in db.sections if x["ref"] == "2")["state"] == "complete"


async def test_a_failing_model_node_ends_as_a_recorded_section_failure() -> None:
    db = section_db(**{"2": ["inference"] * 3})  # the draft node fails on every attempt
    async with stack(db) as s:
        outcome = await run_section(s, db)
    assert outcome.state == "failed" and outcome.failure_reason == "inference_unavailable"
    assert db.count("save_section") == 0  # the graph never reached save
    assert db.count("call_model:2") == 3  # the activity's bounded attempts
    assert db.count("fail_section") == 1  # recorded once, after the attempts
    assert next(x for x in db.sections if x["ref"] == "2")["state"] == "failed"


async def test_an_unexpected_error_in_a_node_ends_as_a_recorded_internal_failure() -> None:
    db = section_db()
    async with stack(db) as s:

        async def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError(f"verifier exploded {CANARY}")

        s.api.verify = boom  # type: ignore[method-assign]  # the verify node raises
        outcome = await run_section(s, db)
    assert outcome.state == "failed" and outcome.failure_reason == "internal"
    assert db.count("save_section") == 0 and db.count("fail_section") == 1
    assert next(x for x in db.sections if x["ref"] == "2")["state"] == "failed"
    assert CANARY not in repr(outcome)  # the message of the cause travels nowhere
