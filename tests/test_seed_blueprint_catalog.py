from __future__ import annotations

import json
from pathlib import Path

from agent.services.seed_blueprint_catalog import SeedBlueprintCatalog

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "blueprints" / "seed_blueprint_catalog.v1.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _catalog_payload() -> dict:
    return {
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
                        "config": {"responsibility": "lead"},
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


def test_seed_blueprint_catalog_loads_and_exposes_seed_map(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    _write_json(catalog_path, _catalog_payload())
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    seed_map = service.as_seed_blueprint_map()

    assert list(seed_map.keys()) == ["Tiny"]
    assert seed_map["Tiny"]["base_team_type_name"] == "Tiny"
    assert seed_map["Tiny"]["roles"][0]["template_name"] == "Tiny - Lead"
    assert seed_map["Tiny"]["artifacts"][0]["title"] == "Kickoff"


def test_seed_blueprint_catalog_handles_missing_file_without_raising(tmp_path: Path) -> None:
    service = SeedBlueprintCatalog(
        catalog_path=tmp_path / "missing.json",
        schema_path=SCHEMA_PATH,
        repository_root=tmp_path,
    )

    assert service.as_seed_blueprint_map() == {}
    assert service.load_error is not None


def test_seed_blueprint_catalog_rejects_duplicate_role_or_artifact_order(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _catalog_payload()
    payload["blueprints"][0]["roles"].append(
        {
            "name": "Second",
            "description": "Another role",
            "template_name": "Tiny - Second",
            "sort_order": 10,
            "is_required": False,
            "config": {},
        }
    )
    payload["blueprints"][0]["artifacts"].append(
        {
            "kind": "task",
            "title": "Second Task",
            "description": "Another artifact",
            "sort_order": 10,
            "payload": {"status": "todo", "priority": "Low"},
        }
    )
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)

    assert service.as_seed_blueprint_map() == {}
    assert "duplicate role sort_order" in str(service.load_error or "")
