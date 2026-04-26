from __future__ import annotations

import json
from pathlib import Path

from agent.services.planning_template_catalog import PlanningTemplateCatalog

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "planning" / "planning_template_catalog.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _catalog_payload() -> dict:
    return {
        "schema": "planning_template_catalog.v1",
        "version": "1.0.0",
        "templates": [
            {
                "id": "bug_fix",
                "title": "Bug Fix",
                "keywords": ["bug", "fix"],
                "related_standard_blueprints": ["Code-Repair"],
                "subtasks": [
                    {
                        "title": "Bug reproduzieren",
                        "description": "Issue reproduzieren und dokumentieren.",
                        "priority": "High",
                    }
                ],
            },
            {
                "id": "feature",
                "title": "Feature",
                "keywords": ["feature", "add"],
                "related_standard_blueprints": ["Scrum"],
                "subtasks": [
                    {
                        "title": "Feature implementieren",
                        "description": "Neue Funktion implementieren.",
                        "priority": "Medium",
                    }
                ],
            },
        ],
    }


def test_planning_template_catalog_resolves_exact_template_id(tmp_path: Path) -> None:
    catalog_path = tmp_path / "planning_templates.json"
    _write_json(catalog_path, _catalog_payload())
    service = PlanningTemplateCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    subtasks = service.resolve_subtasks("bug_fix")

    assert subtasks is not None
    assert subtasks[0]["title"] == "Bug reproduzieren"
    assert subtasks[0]["priority"] == "High"


def test_planning_template_catalog_resolves_by_keyword(tmp_path: Path) -> None:
    catalog_path = tmp_path / "planning_templates.json"
    _write_json(catalog_path, _catalog_payload())
    service = PlanningTemplateCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    subtasks = service.resolve_subtasks("Please fix this production bug in auth")

    assert subtasks is not None
    assert subtasks[0]["title"] == "Bug reproduzieren"


def test_planning_template_catalog_returns_none_for_unknown_template(tmp_path: Path) -> None:
    catalog_path = tmp_path / "planning_templates.json"
    _write_json(catalog_path, _catalog_payload())
    service = PlanningTemplateCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    assert service.resolve_subtasks("no matching keyword here") is None
    assert service.get_template("does_not_exist") is None


def test_planning_template_catalog_handles_invalid_catalog_without_raising_in_resolution(tmp_path: Path) -> None:
    catalog_path = tmp_path / "planning_templates.json"
    _write_json(
        catalog_path,
        {
            "schema": "planning_template_catalog.v1",
            "version": "1.0.0",
            "templates": [
                {
                    "id": "broken",
                    "keywords": ["x"],
                    "subtasks": [{"title": "Broken", "description": "Broken", "priority": "Urgent"}],
                }
            ],
        },
    )
    service = PlanningTemplateCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    assert service.resolve_subtasks("broken") is None
    assert service.load_error is not None


def test_planning_template_catalog_keeps_deterministic_template_order_for_keyword_matching(tmp_path: Path) -> None:
    catalog_path = tmp_path / "planning_templates.json"
    payload = _catalog_payload()
    payload["templates"][0]["keywords"] = ["shared"]
    payload["templates"][1]["keywords"] = ["shared"]
    _write_json(catalog_path, payload)
    service = PlanningTemplateCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    template = service.resolve_template("shared behavior", exact_id_first=False)

    assert template is not None
    assert template["id"] == "bug_fix"
