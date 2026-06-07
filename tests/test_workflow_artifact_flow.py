"""Tests for the workflow artifact-flow service (WFG-016)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.workflow_artifact_flow import (  # noqa: E402
    ArtifactFlowReport,
    ArtifactFlowViolation,
    ArtifactRef,
    WORKFLOW_ARTIFACT_GRAPH_SCHEMA,
    evaluate_artifact_blocker,
    filter_worker_artifact_refs,
    resolve_required_artifact_refs,
    step_consumes,
    step_produces,
    validate_workflow_artifact_graph,
)


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------


class TestNormalizers:
    def test_step_consumes_accepts_list_of_strings(self):
        step = {"consumes": ["execution_plan", "task_breakdown"]}
        refs = step_consumes(step)
        assert [r.key for r in refs] == ["execution_plan", "task_breakdown"]
        assert refs[0].type == ""
        assert refs[0].optional is False

    def test_step_consumes_accepts_list_of_dicts(self):
        step = {"consumes": [
            {"key": "execution_plan", "type": "plan", "optional": False},
            {"key": "design_doc", "optional": True},
        ]}
        refs = step_consumes(step)
        assert [r.key for r in refs] == ["execution_plan", "design_doc"]
        assert refs[0].type == "plan"
        assert refs[1].optional is True

    def test_step_consumes_accepts_dict(self):
        step = {"consumes": {
            "execution_plan": {"type": "plan"},
            "design_doc": True,  # truthy = optional
        }}
        refs = step_consumes(step)
        keys = {r.key for r in refs}
        assert keys == {"execution_plan", "design_doc"}
        design = next(r for r in refs if r.key == "design_doc")
        assert design.optional is True

    def test_step_consumes_drops_whitespace_and_dedupes(self):
        step = {"consumes": ["  ", "a", "a", "b"]}
        assert [r.key for r in step_consumes(step)] == ["a", "b"]

    def test_step_consumes_handles_non_dict_step(self):
        assert step_consumes(None) == []
        assert step_consumes("not a step") == []

    def test_step_produces_mirrors_consumes(self):
        step = {"produces": ["code_changes", "implementation_notes"]}
        assert [r.key for r in step_produces(step)] == [
            "code_changes", "implementation_notes"
        ]

    def test_step_produces_handles_non_dict_step(self):
        assert step_produces(None) == []


# ---------------------------------------------------------------------------
# validate_workflow_artifact_graph
# ---------------------------------------------------------------------------


class TestValidateArtifactGraph:
    def test_happy_path_no_violations(self):
        steps = [
            {"id": "plan", "produces": ["execution_plan"]},
            {"id": "impl", "consumes": ["execution_plan"], "produces": ["code_changes"]},
            {"id": "qa", "consumes": ["code_changes"], "produces": ["verification_report"]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        assert report.is_valid
        assert report.violations == ()

    def test_missing_producer_reported(self):
        steps = [
            {"id": "impl", "consumes": ["execution_plan"], "produces": ["code_changes"]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        assert not report.is_valid
        assert len(report.violations) == 1
        v = report.violations[0]
        assert v.step_id == "impl"
        assert v.missing_key == "execution_plan"
        assert v.reason == "no_producer"

    def test_seed_artifacts_satisfy_consumes(self):
        steps = [
            {"id": "impl", "consumes": ["goal_brief"], "produces": ["code_changes"]},
        ]
        report = validate_workflow_artifact_graph(
            steps=steps, goal_seed_artifact_keys=["goal_brief", "acceptance_criteria"]
        )
        assert report.is_valid

    def test_optional_consume_does_not_block(self):
        steps = [
            {"id": "impl", "consumes": [
                {"key": "design_doc", "optional": True},
                {"key": "execution_plan"},
            ]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        assert not report.is_valid
        # only execution_plan is the missing one
        assert [v.missing_key for v in report.violations] == ["execution_plan"]

    def test_type_conflict_on_duplicate_producer_reported(self):
        steps = [
            {"id": "a", "produces": [{"key": "code", "type": "patch"}]},
            {"id": "b", "produces": [{"key": "code", "type": "snippet"}]},
            {"id": "c", "consumes": ["code"]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        # The conflict is a violation on the FIRST producer
        assert any(
            v.reason.startswith("duplicate_key_with_conflicting_type")
            for v in report.violations
        )

    def test_duplicate_producer_with_same_type_ok(self):
        steps = [
            {"id": "a", "produces": [{"key": "code", "type": "patch"}]},
            {"id": "b", "produces": [{"key": "code", "type": "patch"}]},
            {"id": "c", "consumes": ["code"]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        assert report.is_valid

    def test_handles_non_list_input(self):
        report = validate_workflow_artifact_graph(steps=None)
        assert report.is_valid
        assert report.violations == ()

    def test_skips_steps_without_id(self):
        steps = [
            {"produces": ["x"]},  # no id
            {"id": "a", "consumes": ["x"]},  # x exists via un-id'd step, but we can't track it
        ]
        # The un-id'd producer is invisible -> violation
        report = validate_workflow_artifact_graph(steps=steps)
        assert not report.is_valid
        assert report.violations[0].missing_key == "x"

    def test_artifact_keys_contain_step_id(self):
        steps = [
            {"id": "plan", "produces": ["execution_plan"]},
            {"id": "impl", "consumes": ["execution_plan"]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        assert report.producer_by_key["execution_plan"] == "plan"

    def test_report_to_dict(self):
        steps = [
            {"id": "a", "consumes": ["missing"]},
        ]
        report = validate_workflow_artifact_graph(steps=steps)
        d = report.to_dict()
        assert d["schema"] == WORKFLOW_ARTIFACT_GRAPH_SCHEMA
        assert d["is_valid"] is False
        assert len(d["violations"]) == 1


# ---------------------------------------------------------------------------
# resolve_required_artifact_refs / filter_worker_artifact_refs
# ---------------------------------------------------------------------------


class TestArtifactAllowlist:
    def test_consumes_only(self):
        step = {"consumes": ["execution_plan", "task_breakdown"]}
        refs = resolve_required_artifact_refs(
            step=step, goal_seed_artifact_keys=[]
        )
        assert [r.key for r in refs] == ["execution_plan", "task_breakdown"]

    def test_consumes_plus_seed(self):
        step = {"consumes": ["execution_plan"]}
        refs = resolve_required_artifact_refs(
            step=step, goal_seed_artifact_keys=["goal_brief", "acceptance_criteria"]
        )
        keys = [r.key for r in refs]
        assert "execution_plan" in keys
        assert "goal_brief" in keys
        assert "acceptance_criteria" in keys

    def test_no_consumes_returns_seed_only(self):
        step = {"id": "a"}  # no consumes
        refs = resolve_required_artifact_refs(
            step=step, goal_seed_artifact_keys=["goal_brief"]
        )
        assert [r.key for r in refs] == ["goal_brief"]

    def test_no_consumes_no_seed_returns_empty(self):
        step = {"id": "a"}
        refs = resolve_required_artifact_refs(step=step, goal_seed_artifact_keys=[])
        assert refs == []

    def test_non_dict_step_returns_empty(self):
        assert resolve_required_artifact_refs(step=None) == []

    def test_optional_consume_included_when_no_seed_match(self):
        step = {"consumes": [{"key": "design_doc", "optional": True}]}
        refs = resolve_required_artifact_refs(
            step=step, goal_seed_artifact_keys=[]
        )
        # Optional consumes without seed match are filtered out
        assert refs == []

    def test_filter_worker_artifact_refs_keeps_allowed_only(self):
        step = {"consumes": ["execution_plan"]}
        kept = filter_worker_artifact_refs(
            step=step,
            candidate_refs=[
                "execution_plan", "task_breakdown", "random", "goal_brief",
            ],
            goal_seed_artifact_keys=["goal_brief"],
        )
        # execution_plan (consume) and goal_brief (seed) are kept
        assert "execution_plan" in kept
        assert "goal_brief" in kept
        assert "task_breakdown" not in kept
        assert "random" not in kept

    def test_filter_worker_artifact_refs_empty_candidates(self):
        step = {"consumes": ["x"]}
        assert filter_worker_artifact_refs(
            step=step, candidate_refs=[]
        ) == []
        assert filter_worker_artifact_refs(step=step, candidate_refs=None) == []

    def test_filter_worker_artifact_refs_drops_whitespace(self):
        step = {"consumes": ["x"]}
        kept = filter_worker_artifact_refs(
            step=step, candidate_refs=["x", "  ", ""], goal_seed_artifact_keys=[]
        )
        assert kept == ["x"]


# ---------------------------------------------------------------------------
# evaluate_artifact_blocker
# ---------------------------------------------------------------------------


class TestArtifactBlocker:
    def test_artifact_flow_disabled_short_circuits(self):
        step = {"consumes": ["missing_artifact"]}
        result = evaluate_artifact_blocker(
            step=step, produced_artifact_keys=[], artifact_flow_enabled=False
        )
        assert result["blocked"] is False
        assert result["reason_code"] == "artifact_flow_disabled"

    def test_step_without_consumes_is_not_blocked(self):
        step = {"id": "a"}
        result = evaluate_artifact_blocker(
            step=step, produced_artifact_keys=[], artifact_flow_enabled=True
        )
        assert result["blocked"] is False
        assert result["reason_code"] == "no_consumes_declared"

    def test_consumes_satisfied_by_producer(self):
        step = {"consumes": ["execution_plan"]}
        result = evaluate_artifact_blocker(
            step=step, produced_artifact_keys=["execution_plan"],
        )
        assert result["blocked"] is False
        assert result["reason_code"] == "ok"

    def test_consumes_satisfied_by_seed(self):
        step = {"consumes": ["goal_brief"]}
        result = evaluate_artifact_blocker(
            step=step,
            produced_artifact_keys=[],
            goal_seed_artifact_keys=["goal_brief"],
        )
        assert result["blocked"] is False
        assert result["reason_code"] == "ok"

    def test_missing_required_blocks(self):
        step = {"consumes": ["execution_plan"]}
        result = evaluate_artifact_blocker(
            step=step, produced_artifact_keys=[]
        )
        assert result["blocked"] is True
        assert result["reason_code"] == "missing_artifacts"
        assert "execution_plan" in [m["key"] for m in result["missing_consumes"]]

    def test_optional_consume_missing_does_not_block(self):
        step = {"consumes": [{"key": "design_doc", "optional": True}]}
        result = evaluate_artifact_blocker(
            step=step, produced_artifact_keys=[]
        )
        assert result["blocked"] is False

    def test_non_dict_step_returns_ok(self):
        result = evaluate_artifact_blocker(step=None)
        assert result["blocked"] is False
        assert result["reason_code"] == "ok"

    def test_missing_consume_recorded_with_type(self):
        step = {"consumes": [{"key": "execution_plan", "type": "plan"}]}
        result = evaluate_artifact_blocker(step=step, produced_artifact_keys=[])
        assert result["blocked"] is True
        miss = result["missing_consumes"][0]
        assert miss["key"] == "execution_plan"
        assert miss["type"] == "plan"
        assert miss["optional"] is False


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_artifact_ref_to_dict(self):
        ref = ArtifactRef(key="x", type="plan", optional=True)
        assert ref.to_dict() == {"key": "x", "type": "plan", "optional": True}

    def test_violation_to_dict(self):
        v = ArtifactFlowViolation(step_id="s", missing_key="k", reason="r")
        assert v.to_dict() == {"step_id": "s", "missing_key": "k", "reason": "r"}

    def test_report_is_valid(self):
        empty = ArtifactFlowReport()
        assert empty.is_valid
        not_empty = ArtifactFlowReport(violations=(
            ArtifactFlowViolation(step_id="s", missing_key="k", reason="r"),
        ))
        assert not not_empty.is_valid

    def test_report_to_dict_json_serializable(self):
        import json
        report = ArtifactFlowReport(violations=(
            ArtifactFlowViolation(step_id="s", missing_key="k", reason="r"),
        ))
        json.dumps(report.to_dict())
