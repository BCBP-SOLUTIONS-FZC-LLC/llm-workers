"""The data converter in the loop: history is unreadable, the run still works (LLD WS-12, WS-13)."""

import pytest
from fake_engine import FakeDb, make_input
from harness import stack
from history import family, history_contains
from llm_workers_generation.codec.failure import build_data_converter
from llm_workers_generation.codec.payload_codec import StaticKeyProvider

pytestmark = pytest.mark.integration


async def test_run_completes_with_encrypting_converter_and_history_is_opaque() -> None:
    keys = StaticKeyProvider({"k1": b"k" * 32}, "k1")
    db = FakeDb()
    async with stack(db, converter=build_data_converter(keys)) as s:
        raw = make_input(outline_gate=False)
        handle = await s.client.start_workflow(
            "GenerationWorkflow", raw, id=raw["artefact_id"], task_queue="llm-generation"
        )
        result = await handle.result()  # decoded by the same converter
        assert result["status"] == "complete"
        histories = await family(s.client, handle, raw["tenant_id"])
        for h in histories:
            # without the key, not even an identifier is readable (the codec encrypts every payload)
            assert not history_contains(h, raw["tenant_id"], decode=True)
            assert not history_contains(h, raw["artefact_id"].replace("-", ""), decode=True)
        text = histories[0].to_json()
        assert "encryption-key-id" in text
