import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "integrations" / "open_notebook_export.v1.json"
FIXTURES = ROOT / "tests" / "fixtures" / "open_notebook"


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _errors(payload: dict) -> list[str]:
    return [error.message for error in _validator().iter_errors(payload)]


def test_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def test_minimal_export_validates():
    assert _errors(_load_fixture("minimal_export.json")) == []


def test_complex_export_validates():
    assert _errors(_load_fixture("complex_export.json")) == []


def test_missing_source_id_is_rejected():
    assert _errors(_load_fixture("invalid_export_missing_source_id.json"))


def test_source_requires_usable_content_source():
    payload = _load_fixture("minimal_export.json")
    payload["sources"][0]["full_text"] = ""
    payload["sources"][0]["asset"] = {}
    assert _errors(payload)


def test_source_url_only_is_accepted():
    payload = _load_fixture("minimal_export.json")
    payload["sources"][0].pop("full_text")
    payload["sources"][0]["asset"] = {"url": "https://example.org/doc"}
    assert _errors(payload) == []


def test_unknown_top_level_field_is_rejected():
    payload = _load_fixture("minimal_export.json")
    payload["surprise"] = True
    assert _errors(payload)


def test_metadata_extension_fields_are_allowed():
    payload = _load_fixture("minimal_export.json")
    payload["metadata"] = {"exporter": "custom", "flags": {"beta": True}}
    payload["sources"][0]["metadata"] = {"anything": [1, 2, 3]}
    assert _errors(payload) == []


@pytest.mark.parametrize("field", ["schema", "export_version", "source_system"])
def test_required_top_level_fields(field):
    payload = _load_fixture("minimal_export.json")
    payload.pop(field)
    assert _errors(payload)
