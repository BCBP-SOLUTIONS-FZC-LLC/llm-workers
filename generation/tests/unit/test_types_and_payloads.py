"""Payload types hold identifiers only (LLD §4.3, ASYNC-12, WP-9)."""

import dataclasses
import inspect

import llm_models.workers as plain
import llm_workers_generation.types as ours

FORBIDDEN = (
    "text",
    "content",
    "prompt",
    "chunk",
    "instruction",
    "message",
    "trace",
    "detail",
    "body",
    "draft",
)
# the one legitimate near-match: a reference to the reviewer's words, never the words
ALLOWED = {"instruction_ref", "trace_id"}


def _fields(module: object) -> list[tuple[str, str]]:
    out = []
    for cls_name, cls in inspect.getmembers(module, inspect.isclass):
        if dataclasses.is_dataclass(cls) and cls.__module__ == module.__name__:  # type: ignore[attr-defined]
            out += [(cls_name, f.name) for f in dataclasses.fields(cls)]
    return out


def test_no_payload_field_can_hold_content() -> None:
    for module in (plain, ours):
        for cls, name in _fields(module):
            if name in ALLOWED:
                continue
            assert not any(word in name for word in FORBIDDEN), f"{cls}.{name} looks like content"


def test_run_input_decodes_stored_and_wire_forms() -> None:
    from fake_engine import make_input

    wire = make_input(instruction_ref="r1")
    stored = make_input(instruction="NEVER-IN-HISTORY")
    assert plain.GenerationWorkflowInput.from_dict(wire).instruction_ref == "r1"
    decoded = plain.GenerationWorkflowInput.from_dict(stored)
    assert decoded.instruction_ref == stored["generation_run_id"]  # the words become a reference
    assert "NEVER-IN-HISTORY" not in repr(decoded)


def test_unknown_keys_do_not_fail_a_run() -> None:
    from fake_engine import make_input

    inp = plain.GenerationWorkflowInput.from_dict(make_input(schema_ref="s1", future_key="x"))
    assert inp.extras["schema_ref"] == "s1"


def test_activity_results_default_to_quiet() -> None:
    assert ours.StepResult().ok and not ours.StepResult().quota_suspended
    assert ours.SlotLease("r", False).expires_at == 0.0
