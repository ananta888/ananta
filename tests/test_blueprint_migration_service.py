"""Tests for the legacy blueprint migration service (WFG-021)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.blueprint_migration_service import (  # noqa: E402
    LegacyBlueprintMigrationError,
    migrate_legacy_blueprint,
    migrate_legacy_blueprint_file,
)


# ---------------------------------------------------------------------------
# migrate_legacy_blueprint
# ---------------------------------------------------------------------------


class TestMigrateLegacyBlueprint:
    def test_strict_mode_raises_on_missing_name(self):
        with pytest.raises(LegacyBlueprintMigrationError):
            migrate_legacy_blueprint(blueprint={}, strict=True)

    def test_strict_mode_raises_on_no_artifacts(self):
        with pytest.raises(LegacyBlueprintMigrationError):
            migrate_legacy_blueprint(
                blueprint={"name": "X", "artifacts": []}, strict=True
            )

    def test_non_strict_returns_warnings(self):
        result = migrate_legacy_blueprint(
            blueprint={"name": ""}, strict=False
        )
        assert "warnings" in result["migration_note"]
        assert result["migration_note"]["warnings"]

    def test_simple_blueprint_produces_workflow(self):
        bp = {
            "name": "Scrum",
            "roles": [
                {"name": "PO", "is_required": True, "config": {"responsibility": "backlog"}},
                {"name": "Dev", "is_required": True, "config": {"responsibility": "delivery"}},
            ],
            "artifacts": [
                {"title": "Plan Sprint", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["execution_plan"]}},
                {"title": "Implement", "kind": "task", "sort_order": 20,
                 "payload": {"produces": ["code_changes"]}},
                {"title": "Review", "kind": "task", "sort_order": 30,
                 "payload": {"produces": ["signoff"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        wf = result["workflow"]
        assert wf["schema"] == "blueprint_workflow.v1"
        assert wf["id"] == "scrum"
        step_ids = [s["id"] for s in wf["steps"]]
        assert step_ids == ["plan_sprint", "implement", "review"]
        # The implementation step's consumes include the
        # previous step's produces.
        impl = next(s for s in wf["steps"] if s["id"] == "implement")
        assert "execution_plan" in impl["consumes"]
        # The last step is a gate with a file_exists check.
        review = wf["steps"][-1]
        assert review["gate"] is True
        assert any(c["type"] == "file_exists" for c in review["checks"])

    def test_policy_artifact_excluded(self):
        bp = {
            "name": "X",
            "artifacts": [
                {"title": "Default Policy", "kind": "policy", "sort_order": 100,
                 "payload": {"produces": []}},
                {"title": "Implement", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["code"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        step_titles = [s["task_ref"] for s in result["workflow"]["steps"]]
        assert "Default Policy" not in step_titles
        assert "Implement" in step_titles

    def test_role_assigned_per_step_kind(self):
        bp = {
            "name": "X",
            "roles": [
                {"name": "Planner", "is_required": True, "config": {"responsibility": "backlog"}},
                {"name": "Coder", "is_required": True, "config": {"responsibility": "delivery"}},
            ],
            "artifacts": [
                {"title": "Plan", "kind": "task", "sort_order": 10, "payload": {"produces": ["plan"]}},
                {"title": "Build", "kind": "task", "sort_order": 20, "payload": {"produces": ["code"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        plan_step = result["workflow"]["steps"][0]
        build_step = result["workflow"]["steps"][1]
        assert plan_step["role"] == "Planner"
        assert build_step["role"] == "Coder"

    def test_task_kind_inferred(self):
        bp = {
            "name": "X",
            "artifacts": [
                {"title": "Plan Sprint", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["plan"]}},
                {"title": "Fix Bug", "kind": "task", "sort_order": 20,
                 "payload": {"produces": ["code"]}},
                {"title": "Verify QA", "kind": "task", "sort_order": 30,
                 "payload": {"produces": ["qa"]}},
                {"title": "Review Signoff", "kind": "task", "sort_order": 40,
                 "payload": {"produces": ["signoff"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        kinds = [s["task_kind"] for s in result["workflow"]["steps"]]
        assert kinds[0] == "planning"
        assert kinds[1] == "coding"
        assert kinds[2] == "verification"
        assert kinds[3] == "review"

    def test_gate_uses_goal_state_when_no_produces(self):
        bp = {
            "name": "X",
            "artifacts": [
                {"title": "Plan", "kind": "task", "sort_order": 10, "payload": {}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        gate = result["workflow"]["steps"][0]
        assert gate["gate"] is True
        # Last step with no produces gets a goal_state check
        assert any(c["type"] == "goal_state" for c in gate["checks"])

    def test_seed_artifact_keys_injected(self):
        bp = {
            "name": "Code-Repair",
            "artifacts": [
                {"title": "Triage", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["repro"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        # Code-Repair name should pick up incident_report as a seed
        assert "incident_report" in result["workflow"]["seed_artifact_keys"]
        # Seed keys are added to the first step's consumes
        first = result["workflow"]["steps"][0]
        assert "incident_report" in first["consumes"]
        assert "goal_brief" in first["consumes"]

    def test_no_role_returns_worker_fallback(self):
        bp = {
            "name": "X",
            "artifacts": [
                {"title": "Do Stuff", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["x"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        assert result["workflow"]["steps"][0]["role"] == "Worker"

    def test_sort_order_respected(self):
        bp = {
            "name": "X",
            "artifacts": [
                {"title": "B", "kind": "task", "sort_order": 20, "payload": {"produces": ["b"]}},
                {"title": "A", "kind": "task", "sort_order": 10, "payload": {"produces": ["a"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        assert [s["id"] for s in result["workflow"]["steps"]] == ["a", "b"]

    def test_depends_on_inferred(self):
        bp = {
            "name": "X",
            "artifacts": [
                {"title": "Plan", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["plan"]}},
                {"title": "Build", "kind": "task", "sort_order": 20,
                 "payload": {"produces": ["code"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        plan_step = result["workflow"]["steps"][0]
        build_step = result["workflow"]["steps"][1]
        assert plan_step["depends_on"] == []
        assert build_step["depends_on"] == ["plan"]

    def test_migration_note_present(self):
        bp = {"name": "X", "artifacts": [
            {"title": "P", "kind": "task", "sort_order": 10, "payload": {"produces": ["p"]}},
        ]}
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        note = result["migration_note"]
        assert note["blueprint_name"] == "X"
        assert note["generated_step_count"] == 1
        assert note["strict"] is False

    def test_warns_when_no_required_roles(self):
        bp = {
            "name": "X",
            "roles": [{"name": "Optional", "is_required": False, "config": {}}],
            "artifacts": [
                {"title": "A", "kind": "task", "sort_order": 10, "payload": {"produces": ["a"]}},
            ],
        }
        result = migrate_legacy_blueprint(blueprint=bp, strict=False)
        assert any("is_required" in w for w in result["migration_note"]["warnings"])


# ---------------------------------------------------------------------------
# migrate_legacy_blueprint_file
# ---------------------------------------------------------------------------


class TestMigrateLegacyBlueprintFile:
    def test_writes_side_by_side(self, tmp_path):
        bp = {
            "name": "Scrum",
            "roles": [{"name": "PO", "is_required": True, "config": {"responsibility": "backlog"}}],
            "artifacts": [
                {"title": "Plan", "kind": "task", "sort_order": 10,
                 "payload": {"produces": ["plan"]}},
            ],
        }
        src = tmp_path / "scrum.json"
        src.write_text(json.dumps(bp))
        result = migrate_legacy_blueprint_file(source_path=src)
        out = Path(result["output_path"])
        assert out.exists()
        assert out.name == "scrum.workflow.json"
        body = json.loads(out.read_text())
        assert "workflow" in body
        assert "migration_note" in body

    def test_source_file_not_mutated(self, tmp_path):
        bp = {"name": "X", "artifacts": [
            {"title": "A", "kind": "task", "sort_order": 10, "payload": {"produces": ["a"]}},
        ]}
        src = tmp_path / "x.json"
        src.write_text(json.dumps(bp))
        original = src.read_text()
        migrate_legacy_blueprint_file(source_path=src)
        assert src.read_text() == original

    def test_custom_output_path(self, tmp_path):
        bp = {"name": "X", "artifacts": [
            {"title": "A", "kind": "task", "sort_order": 10, "payload": {"produces": ["a"]}},
        ]}
        src = tmp_path / "x.json"
        src.write_text(json.dumps(bp))
        out = tmp_path / "subdir" / "out.json"
        result = migrate_legacy_blueprint_file(source_path=src, output_path=out)
        assert Path(result["output_path"]).exists()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate_legacy_blueprint_file(source_path=tmp_path / "nope.json")
