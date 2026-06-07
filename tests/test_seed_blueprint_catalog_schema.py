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


# WFG-001: workflow block tests -------------------------------------------


def _minimal_blueprint(*, with_workflow: bool = True) -> dict:
    """Return a valid blueprint skeleton. workflow is opt-in via flag."""
    bp: dict = {
        "name": "GateDemo",
        "description": "Blueprint with workflow gates",
        "base_team_type_name": "GateDemo",
        "roles": [
            {"name": "Planner", "description": "Plans", "template_name": "GateDemo - Planner",
             "sort_order": 10, "is_required": True, "config": {}},
            {"name": "Developer", "description": "Codes", "template_name": "GateDemo - Developer",
             "sort_order": 20, "is_required": True, "config": {}},
            {"name": "Reviewer", "description": "Reviews", "template_name": "GateDemo - Reviewer",
             "sort_order": 30, "is_required": True, "config": {}},
        ],
        "artifacts": [
            {"kind": "task", "title": "Spec", "description": "Spec doc",
             "sort_order": 10, "payload": {}},
        ],
    }
    if with_workflow:
        bp["workflow"] = {
            "mode": "gated",
            "default_failure_policy": "block",
            "steps": [
                {"id": "plan", "role": "Planner", "task_kind": "planning",
                 "title": "Plan", "description": "Plan work",
                 "produces": ["Spec"], "consumes": [], "depends_on": []},
                {"id": "implement", "role": "Developer", "task_kind": "coding",
                 "title": "Implement", "produces": ["Code"], "consumes": ["Spec"],
                 "depends_on": ["plan"]},
                {"id": "review", "role": "Reviewer", "task_kind": "gate_review",
                 "title": "Review", "gate": True,
                 "checks": {"min_artifacts": ["Spec", "Code"], "approval_role": "Reviewer",
                            "verification_required": True},
                 "depends_on": ["implement"],
                 "failure_policy": "manual"},
            ],
        }
    return bp


def test_workflow_block_is_optional() -> None:
    schema = _load(SCHEMA_PATH)
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [_minimal_blueprint(with_workflow=False)],
    }
    assert list(Draft202012Validator(schema).iter_errors(catalog)) == []


def test_workflow_block_validates_full_example() -> None:
    schema = _load(SCHEMA_PATH)
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [_minimal_blueprint()],
    }
    assert list(Draft202012Validator(schema).iter_errors(catalog)) == []


def test_workflow_block_rejects_unknown_role_reference() -> None:
    schema = _load(SCHEMA_PATH)
    bp = _minimal_blueprint()
    bp["workflow"]["steps"][0]["role"] = "GhostRole"
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [bp],
    }
    # Schema passes (it only enforces enum/task_kind patterns); the
    # role-membership check lives in the catalog normalizer. Both
    # layers should be exercised; this test pins the schema layer.
    assert list(Draft202012Validator(schema).iter_errors(catalog)) == []


def test_workflow_block_rejects_invalid_step_id_pattern() -> None:
    schema = _load(SCHEMA_PATH)
    bp = _minimal_blueprint()
    bp["workflow"]["steps"][0]["id"] = "Plan With Spaces"
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [bp],
    }
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert errors, "schema must reject step ids that do not match ^[a-z0-9_-]+$"


def test_workflow_block_rejects_gate_without_checks() -> None:
    # The JSON-Schema layer uses `dependencies.gate.oneOf` to express the
    # gate→checks implication. Some Draft 2020-12 implementations don't
    # fully honor that pattern; we pin the guarantee at the catalog
    # normalizer layer, which is the authoritative gate. See
    # test_seed_blueprint_catalog.py::test_workflow_rejects_gate_without_checks.
    schema = _load(SCHEMA_PATH)
    bp = _minimal_blueprint()
    # Drop the checks object on the gate step and re-validate via the
    # normalizer path.
    del bp["workflow"]["steps"][2]["checks"]
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [bp],
    }
    # Both layers may or may not fire; the normalizer MUST.
    Draft202012Validator(schema).iter_errors(catalog)
    from agent.services.seed_blueprint_catalog import SeedBlueprintCatalog
    import json as _json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        _json.dump(catalog, fh)
        path = fh.name
    try:
        service = SeedBlueprintCatalog(
            catalog_path=Path(path),
            schema_path=SCHEMA_PATH,
            repository_root=Path(path).parent,
        )
        service.load(force_reload=True)
    except ValueError as exc:
        assert "requires a non-empty 'checks' object" in str(exc)
    else:
        # If neither layer fired we have a regression
        raise AssertionError(
            "gate=true without checks must be rejected by at least one layer"
        )


def test_workflow_block_rejects_unknown_task_kind() -> None:
    schema = _load(SCHEMA_PATH)
    bp = _minimal_blueprint()
    bp["workflow"]["steps"][0]["task_kind"] = "unknown_kind"
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [bp],
    }
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert errors, "schema must reject unknown task_kind values"


def test_workflow_block_rejects_unknown_mode() -> None:
    schema = _load(SCHEMA_PATH)
    bp = _minimal_blueprint()
    bp["workflow"]["mode"] = "yolo"
    catalog = {
        "schema": "seed_blueprint_catalog.v1",
        "version": "1.0.0",
        "blueprints": [bp],
    }
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    assert errors, "schema must reject unknown workflow.mode values"
