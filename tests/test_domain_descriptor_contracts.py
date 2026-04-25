from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_SCHEMA_PATH = ROOT / "schemas" / "domain" / "domain_descriptor.v1.json"
STATUS_SCHEMA_PATH = ROOT / "schemas" / "domain" / "domain_status.v1.json"
EXAMPLE_DESCRIPTOR_PATH = ROOT / "domains" / "example" / "domain.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_example_domain_descriptor_validates_against_schema() -> None:
    schema = _load_json(DESCRIPTOR_SCHEMA_PATH)
    payload = _load_json(EXAMPLE_DESCRIPTOR_PATH)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
    assert errors == []


def test_example_descriptor_is_honestly_foundation_only() -> None:
    payload = _load_json(EXAMPLE_DESCRIPTOR_PATH)
    assert payload["lifecycle_status"] == "foundation_only"
    assert payload["runtime_status"] == "descriptor_only"


def test_domain_descriptor_schema_rejects_missing_required_fields() -> None:
    schema = _load_json(DESCRIPTOR_SCHEMA_PATH)
    invalid_payload = {"schema": "domain_descriptor.v1", "domain_id": "example"}
    errors = list(Draft202012Validator(schema).iter_errors(invalid_payload))
    assert errors


def test_domain_status_schema_accepts_runtime_truth_model() -> None:
    schema = _load_json(STATUS_SCHEMA_PATH)
    payload = {
        "schema": "domain_status.v1",
        "domain_id": "example",
        "lifecycle_status": "foundation_only",
        "runtime_status": "descriptor_only",
        "inventory_status": "foundation_only",
        "descriptor_present": True,
        "checked_at": "2026-04-25T15:19:29+02:00",
        "runtime_evidence_refs": [],
        "blockers": [],
        "notes": ["descriptor exists but no runtime evidence yet"],
    }
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors == []


def test_domain_status_schema_rejects_unknown_lifecycle_state() -> None:
    schema = _load_json(STATUS_SCHEMA_PATH)
    payload = {
        "schema": "domain_status.v1",
        "domain_id": "example",
        "lifecycle_status": "runtime_magic",
        "runtime_status": "runtime_available",
        "inventory_status": "runtime_complete",
        "descriptor_present": True,
        "checked_at": "2026-04-25T15:19:29+02:00",
        "runtime_evidence_refs": ["artifacts/domain/example/smoke.log"],
        "blockers": [],
    }
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors
