import json
from pathlib import Path

from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.source_registry import validate_source_descriptor_payload

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "sources" / "open_notebook" / "source_descriptor.json"


def _descriptor() -> dict:
    return json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))


def test_descriptor_validates_against_schema():
    assert validate_source_descriptor_payload(_descriptor()) == []


def test_descriptor_core_fields():
    descriptor = _descriptor()
    assert descriptor["source_id"] == "open-notebook-local-export"
    assert descriptor["source_type"] == "open_notebook"
    assert descriptor["trust_level"] == "user_managed_research"
    assert "official" not in descriptor["trust_level"]


def test_fetch_source_describes_export_without_secrets():
    fetch = _descriptor()["fetch_source"]
    assert fetch["expected_format"] == "open_notebook_export.v1"
    serialized = json.dumps(fetch).lower()
    for forbidden in ("api_key", "token", "password", "secret", "bearer"):
        assert forbidden not in serialized


def test_citation_source_describes_user_managed_workspace():
    citation = _descriptor()["citation_source"]
    assert "user-managed research workspace" in citation["publisher"]
    assert citation["license_ref"] == "unknown"


def test_descriptor_is_registered_as_builtin():
    source_ids = {item["source_id"] for item in load_builtin_source_descriptors()}
    assert "open-notebook-local-export" in source_ids
