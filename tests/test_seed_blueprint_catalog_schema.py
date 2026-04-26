from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config" / "blueprints" / "standard" / "blueprints.json"
SCHEMA_PATH = ROOT / "schemas" / "blueprints" / "seed_blueprint_catalog.v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_seed_blueprint_catalog_schema_validates_repository_catalog() -> None:
    schema = _load(SCHEMA_PATH)
    catalog = _load(CATALOG_PATH)

    assert list(Draft202012Validator(schema).iter_errors(catalog)) == []


def test_seed_blueprint_catalog_schema_validates_minimal_and_full_examples() -> None:
    schema = _load(SCHEMA_PATH)
    validator = Draft202012Validator(schema)

    minimal = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [
            {
                "name": "Tiny",
                "description": "Tiny blueprint",
                "base_team_type_name": "Tiny",
                "roles": [
                    {
                        "name": "Lead",
                        "description": "Leads",
                        "template_name": "Tiny - Lead",
                        "sort_order": 10,
                        "is_required": True,
                        "config": {},
                    }
                ],
                "artifacts": [
                    {
                        "kind": "task",
                        "title": "Kickoff",
                        "description": "Start work",
                        "sort_order": 10,
                        "payload": {"status": "todo", "priority": "High"},
                    }
                ],
            }
        ],
    }
    full = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.1.0",
        "blueprints": [
            {
                "name": "Policy",
                "description": "Includes policy artifacts",
                "base_team_type_name": "Policy",
                "roles": [
                    {
                        "name": "Owner",
                        "description": "Owns policy",
                        "template_name": "Policy - Owner",
                        "sort_order": 10,
                        "is_required": True,
                        "config": {"capability_defaults": ["governance"]},
                    }
                ],
                "artifacts": [
                    {
                        "kind": "policy",
                        "title": "Default Policy",
                        "description": "Policy defaults",
                        "sort_order": 100,
                        "payload": {"security_level": "balanced"},
                    }
                ],
            }
        ],
    }

    assert list(validator.iter_errors(minimal)) == []
    assert list(validator.iter_errors(full)) == []


def test_seed_blueprint_catalog_schema_rejects_invalid_artifact_kind() -> None:
    schema = _load(SCHEMA_PATH)
    validator = Draft202012Validator(schema)

    invalid = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [
            {
                "name": "Invalid",
                "description": "Invalid artifact kind",
                "roles": [
                    {
                        "name": "Lead",
                        "description": "Leads",
                        "template_name": "Invalid - Lead",
                        "sort_order": 10,
                        "is_required": True,
                        "config": {},
                    }
                ],
                "artifacts": [
                    {
                        "kind": "note",
                        "title": "Oops",
                        "description": "Invalid kind",
                        "sort_order": 10,
                        "payload": {},
                    }
                ],
            }
        ],
    }

    assert list(validator.iter_errors(invalid))
