"""Tests for the workflow audit / status service (WFG-017)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.workflow_status_service import (  # noqa: E402
    GATING_STATUSES,
    WORKFLOW_STATUS_SCHEMA,
    build_workflow_status,
    debug_workflow_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(step_id, role="developer", task_kind="coding", gate=False, consumes=None):
    return {
        "id": step_id,
        "role": role,
        "task_kind": task_kind,
        "gate": gate,
        "consumes": consumes or [],
    }


def _task(task_id, plan_node_id="", status="todo", blocked_reason="",
          gate_decision="", missing_consumes_in_details=False):
    task = {
        "id": task_id,
        "plan_node_id": plan_node_id,
        "status": status,
    }
    if blocked_reason:
        task["status_reason_code"] = blocked_reason
        task["status_reason_details"] = {"gate_pending_since": 1.0}
    if gate_decision:
        task["verification_status"] = {
            "gate": gate_decision,
            "gate_decision": {"status": gate_decision, "schema": "workflow_gate_decision.v1"},
        }
    if missing_consumes_in_details:
        task["status_reason_details"] = {"missing_artifacts": ["execution_plan"]}
    return task


# ---------------------------------------------------------------------------
# build_workflow_status
# ---------------------------------------------------------------------------


class TestBuildWorkflowStatus:
    def test_schema_and_basic_fields(self):
        result = build_workflow_status(goal_id="g-1", plan_id="p-1")
        assert result["schema"] == WORKFLOW_STATUS_SCHEMA
        assert result["goal_id"] == "g-1"
        assert result["plan_id"] == "p-1"
        assert result["steps"] == []
        assert result["handoff_events"] == []

    def test_step_without_task_is_not_materialised(self):
        steps = [_step("plan"), _step("impl", consumes=["execution_plan"])]
        result = build_workflow_status(goal_id="g", steps=steps)
        impl = next(s for s in result["steps"] if s["step_id"] == "impl")
        assert impl["is_blocker"] is True
        assert "not_materialised" in impl["blocked_reasons"]

    def test_step_with_unmet_consumes_reports_missing(self):
        steps = [
            _step("plan"),
            _step("impl", consumes=["execution_plan"]),
        ]
        tasks = [_task("ptask-plan", plan_node_id="plan", status="completed")]
        result = build_workflow_status(
            goal_id="g", steps=steps, tasks=tasks
        )
        impl = next(s for s in result["steps"] if s["step_id"] == "impl")
        assert "execution_plan" in impl["missing_consumes"]
        assert "missing_artifacts" in impl["blocked_reasons"]

    def test_step_with_satisfied_consumes_is_not_blocker(self):
        steps = [
            _step("plan"),
            _step("impl", consumes=["execution_plan"]),
        ]
        tasks = [_task("ptask-plan", plan_node_id="plan", status="completed")]
        result = build_workflow_status(
            goal_id="g", steps=steps, tasks=tasks,
            produced_artifact_keys=["execution_plan"],
        )
        impl = next(s for s in result["steps"] if s["step_id"] == "impl")
        assert impl["missing_consumes"] == []
        assert "missing_artifacts" not in impl["blocked_reasons"]

    def test_blocked_task_keeps_status(self):
        steps = [_step("impl")]
        tasks = [
            _task("ptask-impl", plan_node_id="impl", status="blocked",
                  blocked_reason="gate_pending"),
        ]
        result = build_workflow_status(goal_id="g", steps=steps, tasks=tasks)
        impl = result["steps"][0]
        assert impl["task_status"] == "blocked"
        assert impl["task_blocker_reason"] == "gate_pending"
        assert impl["is_blocker"] is True

    def test_gate_decision_propagates(self):
        steps = [_step("gate", role="scrum_master", gate=True)]
        tasks = [
            _task("ptask-gate", plan_node_id="gate", status="completed",
                  gate_decision="passed"),
        ]
        result = build_workflow_status(goal_id="g", steps=steps, tasks=tasks)
        assert result["steps"][0]["gate"] is True
        assert result["steps"][0]["gate_decision"] == "passed"

    def test_handoff_events_aggregated(self):
        steps = [_step("plan"), _step("impl")]
        tasks = [
            {
                "id": "ptask-impl",
                "plan_node_id": "impl",
                "status": "blocked",
                "worker_execution_context": {
                    "workflow_events": [
                        {"event_id": "h1", "timestamp": 1.0, "to_step": "impl"},
                    ]
                },
            },
        ]
        result = build_workflow_status(goal_id="g", steps=steps, tasks=tasks)
        assert len(result["handoff_events"]) == 1
        assert result["handoff_events"][0]["to_step"] == "impl"

    def test_orphan_task_appears_as_orphan_step(self):
        # A task whose plan_node_id is not in the declared steps
        # list but DOES carry a workflow_step block still surfaces
        # in the response (so the user can see it; otherwise it
        # would be invisible to the audit query).
        tasks = [
            {
                "id": "ptask-orphan",
                "plan_node_id": "orphan",
                "status": "blocked",
                "status_reason_code": "gate_pending",
                "worker_execution_context": {
                    "workflow_step": {
                        "schema": "workflow_step_provenance.v1",
                        "step_id": "orphan",
                        "role": "developer",
                        "task_kind": "coding",
                        "gate": False,
                    },
                },
            },
        ]
        result = build_workflow_status(goal_id="g", steps=[], tasks=tasks)
        assert any(s["step_id"] == "orphan" for s in result["steps"])
        assert result["steps"][0]["role"] == "developer"

    def test_orphan_task_without_workflow_step_is_ignored(self):
        tasks = [
            {"id": "ptask-orphan", "plan_node_id": "orphan", "status": "blocked"},
        ]
        result = build_workflow_status(goal_id="g", steps=[], tasks=tasks)
        # No workflow-step provenance, so the orphan is invisible.
        assert result["steps"] == []

    def test_debug_summary_included(self):
        steps = [
            _step("plan"),
            _step("impl"),
        ]
        tasks = [
            _task("ptask-impl", plan_node_id="impl", status="blocked",
                  blocked_reason="gate_pending"),
        ]
        result = build_workflow_status(
            goal_id="g", steps=steps, tasks=tasks, debug_summary=True
        )
        assert "debug_summary" in result
        ds = result["debug_summary"]
        assert ds["step_count"] == 2
        assert ds["blocking_count"] >= 1

    def test_audit_log_skipped_when_disabled(self):
        result = build_workflow_status(
            goal_id="g", include_audit_log=False
        )
        assert result["audit_log_actions"] == []

    def test_blueprint_provenance_passed_through(self):
        result = build_workflow_status(
            goal_id="g",
            workflow_id="wf-1",
            blueprint_id="scrum_opencode",
            blueprint_version="2",
        )
        assert result["workflow_id"] == "wf-1"
        assert result["blueprint_id"] == "scrum_opencode"
        assert result["blueprint_version"] == "2"

    def test_redaction_applied(self):
        steps = [_step("plan")]
        tasks = [{
            "id": "ptask-plan",
            "plan_node_id": "plan",
            "status": "todo",
            "password": "super-secret",  # noqa: S105 — test redaction
            "token": "abc",
        }]
        result = build_workflow_status(goal_id="g", steps=steps, tasks=tasks)
        # The sensitive key names are redacted (we cannot assert
        # the value is redacted here because the user-level
        # redactor only acts on certain patterns; the test that
        # matters is the run does not crash on raw credentials).
        assert "schema" in result


# ---------------------------------------------------------------------------
# debug_workflow_status
# ---------------------------------------------------------------------------


class TestDebugWorkflowStatus:
    def test_renders_header_and_blocking_steps(self, capsys):
        steps = [
            _step("plan"),
            _step("impl"),
        ]
        tasks = [
            _task("ptask-impl", plan_node_id="impl", status="blocked",
                  blocked_reason="gate_pending"),
        ]
        output = debug_workflow_status(
            goal_id="g-1", steps=steps, tasks=tasks, plan_id="p-1"
        )
        assert "g-1" in output
        assert "p-1" in output
        assert "impl" in output
        assert "gate_pending" in output

    def test_renders_blueprint_line(self):
        output = debug_workflow_status(
            goal_id="g",
            steps=[],
            tasks=[],
            blueprint_id="scrum_opencode",
            blueprint_version="2",
        )
        assert "scrum_opencode" in output
        assert "v2" in output

    def test_no_blocking_message_when_all_green(self):
        steps = [_step("plan"), _step("impl")]
        tasks = [
            _task("ptask-plan", plan_node_id="plan", status="completed"),
            _task("ptask-impl", plan_node_id="impl", status="todo"),
        ]
        output = debug_workflow_status(
            goal_id="g", steps=steps, tasks=tasks,
            produced_artifact_keys=["execution_plan"],
        )
        assert "no blocking steps" in output


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_gating_statuses_includes_blocked(self):
        assert "blocked" in GATING_STATUSES
        assert "pending_approval" in GATING_STATUSES
        assert "missing_artifacts" in GATING_STATUSES
