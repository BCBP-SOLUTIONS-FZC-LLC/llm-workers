"""A model answer of the wrong shape is dropped or refused, never an untyped crash (review fix)."""

import json
from uuid import uuid4

import pytest
from fake_engine import FakeDb, FakeEngineApi
from llm_workers_generation.activities.engine_api import ERRORS
from llm_workers_generation.activities.generation import _conflicts, _parse_facts

API = FakeEngineApi(FakeDb())
SECTION = str(uuid4())


def facts(payload: object) -> list:
    return _parse_facts(API, json.dumps(payload), {SECTION}, {"contract_value"})


def test_a_good_fact_is_kept() -> None:
    kept = facts({"facts": [{"key": "contract_value", "section": SECTION, "value": 5}]})
    assert [(f.fact_key, f.value) for f in kept] == [("contract_value", "5")]


@pytest.mark.parametrize(
    "item",
    [
        {"key": ["contract_value"], "section": SECTION, "value": "x"},  # an unhashable key
        {"key": "contract_value", "section": {"a": 1}, "value": "x"},  # an unhashable section
        {"key": "contract_value", "section": SECTION},  # no value
        {"key": "other", "section": SECTION, "value": "x"},  # not a pack key
        {"key": "contract_value", "section": str(uuid4()), "value": "x"},  # not a section here
        "text",
        None,
    ],
)
def test_one_bad_item_is_dropped_and_the_good_one_next_to_it_stays(item: object) -> None:
    good = {"key": "contract_value", "section": SECTION, "value": "7"}
    assert [f.value for f in facts({"facts": [item, good]})] == ["7"]


def test_facts_null_or_absent_is_no_facts() -> None:
    assert facts({"facts": None}) == [] and facts({}) == []


@pytest.mark.parametrize("bad", [{"facts": "x"}, {"facts": {"a": 1}}, [1, 2], "not json"])
def test_a_wrong_overall_shape_is_a_schema_violation_not_an_untyped_error(bad: object) -> None:
    raw = bad if isinstance(bad, str) else json.dumps(bad)
    with pytest.raises(ERRORS.SchemaViolation):
        _parse_facts(API, raw, {SECTION}, {"contract_value"})


def test_conflicts_skip_a_section_whose_values_are_not_an_object() -> None:
    a, b = str(uuid4()), str(uuid4())
    raw = json.dumps({"values": {a: ["nope"], b: {"contract_value": "9"}}})
    assert _conflicts(API, raw, [a, b], {"contract_value"}) == []
    raw = json.dumps({"values": {a: {"contract_value": "1"}, b: {"contract_value": "2"}}})
    assert len(_conflicts(API, raw, [a, b], {"contract_value"})) == 1
    for bad in ({"values": "x"}, {"values": [1]}):
        with pytest.raises(ERRORS.SchemaViolation):
            _conflicts(API, json.dumps(bad), [a], {"contract_value"})
    assert _conflicts(API, json.dumps({"values": None}), [a], {"contract_value"}) == []


def test_deeply_nested_output_is_a_schema_violation_not_a_retried_crash() -> None:
    with pytest.raises(ERRORS.SchemaViolation):
        _parse_facts(API, "[" * 200_000, {SECTION}, {"contract_value"})


def test_a_fact_value_that_is_prose_sized_is_dropped() -> None:
    long = {"key": "contract_value", "section": SECTION, "value": "x" * 5000}
    assert facts({"facts": [long]}) == []


def test_conflicts_ignore_a_value_that_is_a_structure_or_prose_sized() -> None:
    a, b = str(uuid4()), str(uuid4())
    for odd in ({"x": 1}, ["y"], "z" * 5000):
        raw = json.dumps({"values": {a: {"contract_value": "1"}, b: {"contract_value": odd}}})
        assert _conflicts(API, raw, [a, b], {"contract_value"}) == []
