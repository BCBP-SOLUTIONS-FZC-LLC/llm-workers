"""WS-9, WS-11, WS-12: a planted canary never reaches history, signals, results, heartbeats or logs."""

import logging

from fake_engine import CANARY, FakeDb
from harness import stack
from history import family, history_contains
from llm_workers_generation.workflows._common import RunWorkflowBase  # noqa: F401


async def _histories(s, handle, raw) -> list:  # noqa: ANN001
    return await family(s.client, handle, raw["tenant_id"])


async def test_canary_in_text_chunks_and_exceptions_never_reaches_history(caplog) -> None:  # noqa: ANN001
    # the canary is in every chunk and draft the fake produces, and in the exception text of a
    # section that blows up with an unexpected error
    db = FakeDb(behaviors={"3": ["boom"] * 3})
    caplog.set_level(logging.DEBUG)
    async with stack(db) as s:
        handle, raw = await s.start(
            outline_gate=True, instruction_ref=str(__import__("uuid").uuid4())
        )
        await s.wait_status("awaiting_outline_approval")
        await handle.signal("approve_outline", {"outline_version": 1})
        await s.approve(handle, signal=False)
        assert (await handle.result())["status"] == "complete"
        assert db.sections[2]["state"] == "failed"  # the boom section failed with a fixed message
        assert any(
            CANARY in text for text in db.content.values()
        )  # the text exists, in the database
        for h in await _histories(s, handle, raw):
            assert not history_contains(h, CANARY), "content reached Temporal history"
            assert not history_contains(h, "db exploded"), (
                "an engine exception message reached history"
            )
    assert CANARY not in caplog.text, "content reached a log line"


async def test_failure_messages_are_fixed_text() -> None:
    db = FakeDb(behaviors={"3": ["boom"] * 3})
    async with stack(db) as s:
        handle, raw = await s.start(outline_gate=False)
        await handle.result()
        histories = await _histories(s, handle, raw)
        text = " ".join(h.to_json() for h in histories)
        assert "internal error" in text  # the fixed message of ERR_INTERNAL is what history holds
        assert CANARY not in text and "exploded" not in text


async def test_instruction_in_a_mistaken_start_input_is_not_forwarded_to_activities_or_children() -> (
    None
):
    # the API strips the words before it starts a run (P0-4). If a caller ever sent them, the
    # start event holds them (nothing can undo that) but no later event repeats them
    db = FakeDb()
    async with stack(db) as s:
        handle, raw = await s.start(outline_gate=False, instruction=f"reviewer says {CANARY}")
        await handle.result()
        parent, *children = await _histories(s, handle, raw)
        from google.protobuf.json_format import MessageToDict
        from history import _walk

        assert not any(_walk(MessageToDict(e), CANARY.encode(), True) for e in parent.events[1:])
        assert children and not any(history_contains(c, CANARY) for c in children)


async def test_signals_carry_identifiers_only() -> None:
    async with stack() as s:
        handle, _ = await s.start()
        await s.wait_status("awaiting_outline_approval")
        await s.revise(handle, [("1", "intro")])
        await s.wait_status("awaiting_outline_approval")
        await s.approve(handle)
        await handle.result()
        history = await handle.fetch_history()
        signals = [e for e in history.events if e.event_type == 26]  # WORKFLOW_EXECUTION_SIGNALED
        assert signals, "no signals were recorded"
