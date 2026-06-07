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


# WFG-001: workflow block normalizer tests --------------------------------


def _workflow_payload(*, steps: list[dict] | None = None) -> dict:
    base = _catalog_payload()
    base["blueprints"][0]["roles"].append(
        {
            "name": "Dev",
            "description": "Coder",
            "template_name": "Tiny - Dev",
            "sort_order": 20,
            "is_required": True,
            "config": {},
        }
    )
    base["blueprints"][0]["roles"].append(
        {
            "name": "Rev",
            "description": "Reviewer",
            "template_name": "Tiny - Rev",
            "sort_order": 30,
            "is_required": True,
            "config": {},
        }
    )
    if steps is not None:
        base["blueprints"][0]["workflow"] = {
            "mode": "gated",
            "default_failure_policy": "block",
            "steps": steps,
        }
    return base


def _step(*, sid: str, role: str, depends_on: list[str] | None = None, gate: bool = False) -> dict:
    step: dict = {
        "id": sid, "role": role, "task_kind": "planning",
        "title": sid, "description": "",
    }
    if depends_on:
        step["depends_on"] = depends_on
    if gate:
        step["gate"] = True
        step["checks"] = {"min_artifacts": []}
    return step


def test_workflow_absent_yields_none(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    _write_json(catalog_path, _workflow_payload())  # no workflow key
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    payload = service.load()
    assert payload["blueprints"][0]["workflow"] is None


def test_workflow_valid_dag_loads(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _workflow_payload(steps=[
        _step(sid="plan", role="Lead"),
        _step(sid="implement", role="Dev", depends_on=["plan"]),
        _step(sid="review", role="Rev", depends_on=["implement"], gate=True),
    ])
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    blueprint = service.get_blueprint("Tiny")
    assert blueprint is not None
    wf = blueprint["workflow"]
    assert wf is not None
    assert wf["mode"] == "gated"
    assert [s["id"] for s in wf["steps"]] == ["plan", "implement", "review"]


def test_workflow_rejects_unknown_role_reference(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _workflow_payload(steps=[
        _step(sid="plan", role="Ghost"),
    ])
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    assert service.as_seed_blueprint_map() == {}
    assert "is not in the blueprint's roles" in str(service.load_error or "")


def test_workflow_rejects_gate_without_checks(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    bad_step = {
        "id": "loose_gate", "role": "Rev", "task_kind": "gate_review",
        "gate": True, "depends_on": [],
    }
    payload = _workflow_payload(steps=[bad_step])
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    assert service.as_seed_blueprint_map() == {}
    assert "requires a non-empty 'checks' object" in str(service.load_error or "")


def test_workflow_rejects_cycle(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _workflow_payload(steps=[
        _step(sid="a", role="Lead", depends_on=["b"]),
        _step(sid="b", role="Dev", depends_on=["a"]),
    ])
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    assert service.as_seed_blueprint_map() == {}
    assert "cycle" in str(service.load_error or "")


def test_workflow_rejects_unknown_dependency(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _workflow_payload(steps=[
        _step(sid="plan", role="Lead"),
        _step(sid="implement", role="Dev", depends_on=["ghost_step"]),
    ])
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    assert service.as_seed_blueprint_map() == {}
    assert "unknown step" in str(service.load_error or "")


def test_workflow_rejects_duplicate_step_id(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _workflow_payload(steps=[
        _step(sid="plan", role="Lead"),
        _step(sid="plan", role="Dev"),
    ])
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    assert service.as_seed_blueprint_map() == {}
    assert "duplicate workflow step id" in str(service.load_error or "")


def test_workflow_rejects_invalid_mode(tmp_path: Path) -> None:
    catalog_path = tmp_path / "blueprints.json"
    payload = _workflow_payload(steps=[_step(sid="plan", role="Lead")])
    payload["blueprints"][0]["workflow"]["mode"] = "yolo"
    _write_json(catalog_path, payload)
    service = SeedBlueprintCatalog(catalog_path=catalog_path, schema_path=SCHEMA_PATH, repository_root=tmp_path)
    assert service.as_seed_blueprint_map() == {}
    # Either the JSON schema layer or the normalizer can fire first
    # depending on which one rejects the value. Both layers are
    # expected to reject unknown modes.
    msg = str(service.load_error or "")
    assert "yolo" in msg and ("workflow.mode" in msg or "not one of" in msg)
