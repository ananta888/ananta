from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config" / "planning_templates.json"
SCHEMA_PATH = ROOT / "schemas" / "planning" / "planning_template_catalog.v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_planning_template_catalog_schema_validates_repository_catalog() -> None:
    schema = _load(SCHEMA_PATH)
    catalog = _load(CATALOG_PATH)

    assert list(Draft202012Validator(schema).iter_errors(catalog)) == []


def test_planning_template_catalog_schema_validates_minimal_and_full_examples() -> None:
    schema = _load(SCHEMA_PATH)
    validator = Draft202012Validator(schema)

    minimal = {
        "schema": "planning_template_catalog.v1",
        "version": "1.0.0",
        "templates": [
            {
                "id": "bug_fix",
                "keywords": ["bug", "fix"],
                "subtasks": [
                    {
                        "title": "Reproduzieren",
                        "description": "Fehler reproduzieren",
                        "priority": "High",
                    }
                ],
            }
        ],
    }
    full = {
        "schema": "planning_template_catalog.v1",
        "version": "1.1.0",
        "execution_focused_goal_hints": ["python", "pytest"],
        "templates": [
            {
                "id": "admin_repair",
                "title": "Admin Repair",
                "keywords": ["admin_repair", "bounded repair"],
                "related_standard_blueprints": ["Release-Prep", "Security-Review"],
                "subtasks": [
                    {
                        "title": "Scope definieren",
                        "description": "Use-case und Grenzen fixieren.",
                        "priority": "High",
                        "artifact": "admin_repair_scope",
                        "risk_focus": "kein unkontrollierter shell flow",
                        "test_focus": "contract tests",
                        "review_focus": "approval gates",
                    }
                ],
            }
        ],
    }

    assert list(validator.iter_errors(minimal)) == []
    assert list(validator.iter_errors(full)) == []


def test_planning_template_catalog_schema_rejects_invalid_priority() -> None:
    schema = _load(SCHEMA_PATH)
    validator = Draft202012Validator(schema)
    invalid = {
        "schema": "planning_template_catalog.v1",
        "version": "1.0.0",
        "templates": [
            {
                "id": "bug_fix",
                "keywords": ["bug"],
                "subtasks": [
                    {
                        "title": "Reproduzieren",
                        "description": "Fehler reproduzieren",
                        "priority": "Urgent",
                    }
                ],
            }
        ],
    }

    errors = list(validator.iter_errors(invalid))
    assert errors
