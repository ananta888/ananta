"""Integration tests for pattern library ↔ blueprint planner (PAT-013/014/015/019).

Covers:
- PAT-013: seed_blueprint_catalog normalizes pattern_hints in workflow steps
- PAT-013: invalid pattern_hint ids raise ValueError
- PAT-013: preferred ids not in allowed raise ValueError
- PAT-013: blueprints without pattern_hints remain unchanged
- PAT-014: _build_subtasks_from_workflow passes pattern_hints_normalized
- PAT-014: subtasks without hints have no pattern_hints_normalized key
- PAT-014: depends_on DAG unchanged when hints are present
- PAT-019: existing workflow blueprints produce identical subtask cores
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.services.seed_blueprint_catalog import SeedBlueprintCatalog, _normalize_pattern_hints
from agent.services.blueprint_planning_adapter import BlueprintPlanningAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step(
    step_id: str,
    role: str,
    *,
    depends_on: list[str] | None = None,
    pattern_hints: dict | None = None,
) -> MagicMock:
    step = MagicMock()
    step.step_id = step_id
    step.role_name = role
    step.task_kind = "coding"
    step.title = f"Step {step_id}"
    step.description = ""
    step.depends_on = depends_on or []
    step.gate = False
    step.checks = {}
    step.failure_policy = None
    step.required_capabilities = []
    step.produces = []
    step.consumes = []
    step.id = f"db-{step_id}"
    step.pattern_hints = pattern_hints
    return step


# ---------------------------------------------------------------------------
# PAT-013: normalize_pattern_hints
# ---------------------------------------------------------------------------

class TestNormalizePatternHints:
    def test_valid_allowed_and_preferred(self):
        hints = _normalize_pattern_hints(
            {"allowed_patterns": ["python.strategy", "java.strategy"], "preferred_patterns": ["python.strategy"]},
            blueprint_name="bp", step_id="s1",
        )
        assert hints["allowed_patterns"] == ["python.strategy", "java.strategy"]
        assert hints["preferred_patterns"] == ["python.strategy"]
        assert hints["require_tests"] is True

    def test_invalid_pattern_id_rejected(self):
        with pytest.raises(ValueError, match="invalid id"):
            _normalize_pattern_hints(
                {"allowed_patterns": ["INVALID-ID!"]},
                blueprint_name="bp", step_id="s1",
            )

    def test_preferred_not_in_allowed_rejected(self):
        with pytest.raises(ValueError, match="preferred_patterns contains ids not in allowed_patterns"):
            _normalize_pattern_hints(
                {"allowed_patterns": ["java.strategy"], "preferred_patterns": ["python.strategy"]},
                blueprint_name="bp", step_id="s1",
            )

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be an object"):
            _normalize_pattern_hints(["strategy"], blueprint_name="bp", step_id="s1")

    def test_forbid_patterns_stored(self):
        hints = _normalize_pattern_hints(
            {"forbid_patterns": ["java.default_deny_gate"]},
            blueprint_name="bp", step_id="s1",
        )
        assert hints["forbid_patterns"] == ["java.default_deny_gate"]

    def test_require_tests_false(self):
        hints = _normalize_pattern_hints(
            {"require_tests": False},
            blueprint_name="bp", step_id="s1",
        )
        assert hints["require_tests"] is False

    def test_language_targets_normalized(self):
        hints = _normalize_pattern_hints(
            {"language_targets": ["Python", "JAVA"]},
            blueprint_name="bp", step_id="s1",
        )
        assert "python" in hints["language_targets"]
        assert "java" in hints["language_targets"]

    def test_empty_hints_returns_require_tests_only(self):
        hints = _normalize_pattern_hints({}, blueprint_name="bp", step_id="s1")
        assert hints == {"require_tests": True}

    def test_preferred_empty_allowed_no_error(self):
        hints = _normalize_pattern_hints(
            {"preferred_patterns": ["python.strategy"]},
            blueprint_name="bp", step_id="s1",
        )
        assert hints["preferred_patterns"] == ["python.strategy"]


# ---------------------------------------------------------------------------
# PAT-014: _build_subtasks_from_workflow passes hints
# ---------------------------------------------------------------------------

class TestBuildSubtasksPatternHints:
    def test_step_with_hints_propagates_normalized(self):
        hints = {"allowed_patterns": ["python.strategy"], "require_tests": True}
        step = _make_step("s1", "developer", pattern_hints=hints)
        subtasks = BlueprintPlanningAdapter._build_subtasks_from_workflow(
            blueprint_id="bp1",
            blueprint_name="Test BP",
            workflow_steps=[step],
            role_template_hints=[],
        )
        assert len(subtasks) == 1
        assert subtasks[0]["pattern_hints_normalized"] == hints

    def test_step_without_hints_no_key(self):
        step = _make_step("s1", "developer", pattern_hints=None)
        subtasks = BlueprintPlanningAdapter._build_subtasks_from_workflow(
            blueprint_id="bp1",
            blueprint_name="Test BP",
            workflow_steps=[step],
            role_template_hints=[],
        )
        assert "pattern_hints_normalized" not in subtasks[0]

    def test_empty_hints_dict_no_key(self):
        step = _make_step("s1", "developer", pattern_hints={})
        subtasks = BlueprintPlanningAdapter._build_subtasks_from_workflow(
            blueprint_id="bp1",
            blueprint_name="Test BP",
            workflow_steps=[step],
            role_template_hints=[],
        )
        assert "pattern_hints_normalized" not in subtasks[0]

    def test_depends_on_dag_unchanged(self):
        """PAT-019 regression: adding pattern_hints must not change DAG order."""
        hints = {"allowed_patterns": ["python.strategy"]}
        s1 = _make_step("s1", "developer", depends_on=[])
        s2 = _make_step("s2", "reviewer", depends_on=["s1"], pattern_hints=hints)
        subtasks = BlueprintPlanningAdapter._build_subtasks_from_workflow(
            blueprint_id="bp1",
            blueprint_name="Test BP",
            workflow_steps=[s2, s1],  # intentionally reversed for topo-sort test
            role_template_hints=[],
        )
        assert subtasks[0]["blueprint_workflow_step_id_label"] == "s1"
        assert subtasks[1]["blueprint_workflow_step_id_label"] == "s2"
        assert "s1" in subtasks[1]["depends_on"]

    def test_blueprint_without_hints_identical_core(self):
        """PAT-019: blueprint subtask core fields must be identical with/without hints."""
        s_no_hints = _make_step("s1", "developer")
        s_with_hints = _make_step("s1", "developer", pattern_hints={"allowed_patterns": ["python.strategy"]})
        for step in [s_no_hints, s_with_hints]:
            step.task_kind = "coding"
            step.produces = ["artifact_1"]
            step.consumes = []

        sub_no = BlueprintPlanningAdapter._build_subtasks_from_workflow(
            blueprint_id="bp1",
            blueprint_name="My BP",
            workflow_steps=[s_no_hints],
            role_template_hints=[],
        )[0]
        sub_with = BlueprintPlanningAdapter._build_subtasks_from_workflow(
            blueprint_id="bp1",
            blueprint_name="My BP",
            workflow_steps=[s_with_hints],
            role_template_hints=[],
        )[0]

        core_keys = ["title", "task_kind", "gate", "blueprint_id", "blueprint_name", "depends_on", "produces"]
        for key in core_keys:
            assert sub_no.get(key) == sub_with.get(key), f"core field {key!r} differs"
