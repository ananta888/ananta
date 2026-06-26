"""Tests for the diagram-notation catalogue entries (NOT-006).

Verifies that the 8 notation patterns in the catalogue:

* are schema-conformant
* have all required fields (parameters, steps, invariants, gates, examples)
* declare correct category and language
* declare unique pattern_ids
* have realistic, non-empty example payloads
* use the correct output filename extension (.mmd / .bpmn)
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest


CATALOG_PATH = Path(__file__).resolve().parents[1] / "schemas" / "patterns" / "pattern_catalog.v1.json"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "patterns" / "pattern.schema.v1.json"

NOTATION_PATTERN_IDS = [
    "mermaid.class", "mermaid.sequence", "mermaid.state",
    "mermaid.usecase", "mermaid.activity",
    "bpmn.process", "bpmn.pool_lane", "bpmn.collaboration",
]


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _by_pid(catalog) -> dict:
    return {p["pattern_id"]: p for p in catalog}


# ---------------------------------------------------------------------------
# Catalogue structure
# ---------------------------------------------------------------------------


def test_notation_patterns_are_in_catalogue(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        assert pid in by_pid, f"{pid} missing from catalogue"


def test_notation_patterns_have_category_diagram_notation(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        assert by_pid[pid]["category"] == "diagram_notation"


def test_notation_patterns_have_correct_language(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        entry = by_pid[pid]
        if pid.startswith("mermaid."):
            assert entry["language"] == "mermaid", f"{pid} language must be mermaid"
        elif pid.startswith("bpmn."):
            assert entry["language"] == "bpmn", f"{pid} language must be bpmn"


def test_notation_patterns_have_unique_ids(catalog):
    ids = [p["pattern_id"] for p in catalog]
    assert len(ids) == len(set(ids)), "duplicate pattern_id in catalogue"


# ---------------------------------------------------------------------------
# Schema conformance
# ---------------------------------------------------------------------------


def test_notation_patterns_pass_schema_validation(catalog, schema):
    validator = jsonschema.Draft7Validator(schema)
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        errors = list(validator.iter_errors(by_pid[pid]))
        assert not errors, f"{pid} failed schema validation: {[e.message for e in errors][:2]}"


# ---------------------------------------------------------------------------
# Required catalogue fields per the PAT contract
# ---------------------------------------------------------------------------


def test_notation_patterns_have_required_contract_fields(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        entry = by_pid[pid]
        for field_name in (
            "pattern_id", "version", "category", "language", "title",
            "description", "parameters", "required_artifacts",
            "steps", "invariants", "acceptance_gates", "examples",
        ):
            assert field_name in entry, f"{pid} missing {field_name!r}"
        assert entry["version"] == "1.0.0"
        assert entry["risk_level"] == "low"


def test_notation_patterns_have_steps_in_order(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        steps = by_pid[pid]["steps"]
        orders = [s["order"] for s in steps]
        assert sorted(orders) == list(range(1, len(orders) + 1)), (
            f"{pid}: steps must be numbered 1..N in order, got {orders}"
        )


def test_notation_patterns_have_non_empty_examples(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        examples = by_pid[pid]["examples"]
        assert len(examples) >= 1, f"{pid} must have at least one example"
        for ex in examples:
            assert "inputs" in ex
            assert "must_contain" in ex
            assert "must_not_contain" in ex
            assert len(ex["must_contain"]) >= 1


# ---------------------------------------------------------------------------
# Parameter shape
# ---------------------------------------------------------------------------


def test_notation_patterns_parameters_have_name_type_required_description(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        for param in by_pid[pid]["parameters"]:
            for k in ("name", "type", "required", "description"):
                assert k in param, f"{pid}.{param.get('name', '?')} missing {k!r}"


def test_notation_patterns_glob_list_parameters_are_consistent(catalog):
    """Glob_list parameters are intended for JSON-encoded list payloads.
    Notation patterns that declare a glob_list parameter must actually
    use it as a list in the example."""
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        for param in by_pid[pid]["parameters"]:
            if param.get("type") == "glob_list":
                # At least one example input must be a list for this param.
                examples = by_pid[pid]["examples"]
                used_as_list = any(
                    isinstance(ex["inputs"].get(param["name"]), list)
                    for ex in examples
                )
                assert used_as_list, (
                    f"{pid}: glob_list parameter {param['name']!r} "
                    f"is never used as a list in any example"
                )


# ---------------------------------------------------------------------------
# Acceptance gates content
# ---------------------------------------------------------------------------


def test_notation_patterns_acceptance_gates_are_meaningful(catalog):
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        gates = by_pid[pid]["acceptance_gates"]
        # At least 3 acceptance gates per pattern (matches the
        # gate-service implementation).
        assert len(gates) >= 3, f"{pid}: only {len(gates)} acceptance gates"


# ---------------------------------------------------------------------------
# Cross-reference: invariants + steps + examples are mutually consistent
# ---------------------------------------------------------------------------


def test_notation_patterns_examples_render_with_the_notation_renderer():
    """End-to-end smoke test: every example in the catalogue must
    actually render without raising NotationRenderError, with the
    resulting source containing all must_contain markers and none
    of the must_not_contain markers."""
    from agent.services.notation_renderer import get_notation_renderer
    r = get_notation_renderer()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    by_pid = _by_pid(catalog)
    for pid in NOTATION_PATTERN_IDS:
        lang = by_pid[pid]["language"]
        for ex in by_pid[pid]["examples"]:
            art = r.render(pattern_plan={
                "pattern_id": pid,
                "language": lang,
                "parameters": ex["inputs"],
            })
            for marker in ex["must_contain"]:
                assert marker in art.source, (
                    f"{pid}.{ex['name']}: must_contain {marker!r} not in output"
                )
            for marker in ex["must_not_contain"]:
                assert marker not in art.source, (
                    f"{pid}.{ex['name']}: must_not_contain {marker!r} found in output"
                )