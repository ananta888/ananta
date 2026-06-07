"""Tests for the TUI workflow status renderer (WFG-022)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.tui.workflow_status_view import (  # noqa: E402
    render_workflow_status,
    render_workflow_status_text,
)


def _sample_payload():
    return {
        "schema": "workflow_status.v1",
        "goal_id": "g-1",
        "plan_id": "p-1",
        "blueprint_id": "scrum_opencode",
        "blueprint_version": "1",
        "steps": [
            {
                "step_id": "intake",
                "role": "Product Owner",
                "task_id": "ptask-1",
                "task_status": "completed",
                "task_blocker_reason": "",
                "gate": False,
                "gate_decision": "",
                "is_blocker": False,
                "blocked_reasons": [],
                "missing_consumes": [],
            },
            {
                "step_id": "implementation",
                "role": "Developer",
                "task_id": "ptask-4",
                "task_status": "blocked",
                "task_blocker_reason": "gate_pending",
                "gate": False,
                "gate_decision": "",
                "is_blocker": True,
                "blocked_reasons": ["gate_pending"],
                "missing_consumes": [],
            },
            {
                "step_id": "review_gate",
                "role": "Scrum Master",
                "task_id": "ptask-5",
                "task_status": "todo",
                "task_blocker_reason": "",
                "gate": True,
                "gate_decision": "",
                "is_blocker": False,
                "blocked_reasons": [],
                "missing_consumes": [],
            },
        ],
        "handoff_events": [
            {"from_step": "intake", "to_step": "implementation", "status": "created"},
            {"from_step": "implementation", "to_step": "review_gate", "status": "released"},
        ],
        "audit_log_actions": ["workflow_handoff_created", "workflow_handoff_released"],
    }


class TestRenderPlain:
    def test_returns_text_and_summary(self):
        view = render_workflow_status(_sample_payload(), colour=False)
        assert view.text
        assert "g-1" in view.text
        assert "Workflow for goal" in view.text

    def test_marks_blocking_step(self):
        view = render_workflow_status(_sample_payload(), colour=False)
        assert view.has_blocking_step is True
        assert view.blocking_step_count == 1
        assert "[X]" in view.text  # the implementation row

    def test_marks_gate_step(self):
        view = render_workflow_status(_sample_payload(), colour=False)
        assert "[G]" in view.text  # the review_gate row

    def test_renders_handoff_events(self):
        view = render_workflow_status(_sample_payload(), colour=False)
        assert "intake -> implementation" in view.text
        assert "implementation -> review_gate" in view.text

    def test_summarises_more_than_ten_handoffs(self):
        payload = _sample_payload()
        payload["handoff_events"] = [
            {"from_step": "a", "to_step": "b", "status": "created"}
            for _ in range(15)
        ]
        view = render_workflow_status(payload, colour=False)
        assert "and 5 more" in view.text

    def test_renders_without_plan_or_blueprint(self):
        payload = _sample_payload()
        payload["plan_id"] = ""
        payload["blueprint_id"] = ""
        view = render_workflow_status(payload, colour=False)
        assert "Workflow for goal" in view.text

    def test_renders_no_steps(self):
        payload = _sample_payload()
        payload["steps"] = []
        view = render_workflow_status(payload, colour=False)
        assert "no workflow steps" in view.text
        assert view.has_blocking_step is False

    def test_handles_invalid_payload(self):
        view = render_workflow_status(None)  # type: ignore[arg-type]
        assert "no workflow status payload" in view.text
        view = render_workflow_status("not a dict")  # type: ignore[arg-type]
        assert "no workflow status payload" in view.text

    def test_summary_line_for_blocked(self):
        view = render_workflow_status(_sample_payload(), colour=False)
        assert "blocking" in view.summary_line

    def test_summary_line_for_ok(self):
        payload = _sample_payload()
        # Mark all steps as completed
        for s in payload["steps"]:
            s["task_status"] = "completed"
            s["is_blocker"] = False
            s["blocked_reasons"] = []
        view = render_workflow_status(payload, colour=False)
        assert "ok" in view.summary_line
        assert "blocking" not in view.summary_line

    def test_long_role_truncated(self):
        payload = _sample_payload()
        payload["steps"].append({
            "step_id": "review",
            "role": "X" * 60,
            "task_id": "t",
            "task_status": "todo",
            "task_blocker_reason": "",
            "gate": False,
            "gate_decision": "",
            "is_blocker": False,
            "blocked_reasons": [],
            "missing_consumes": [],
        })
        view = render_workflow_status(payload, colour=False)
        # The truncated string contains an ellipsis char
        assert "\u2026" in view.text


class TestRenderColour:
    def test_ansi_codes_present_when_enabled(self):
        view = render_workflow_status(_sample_payload(), colour=True)
        # The header has BOLD
        assert "\x1b[1m" in view.text
        # The blocking step is RED
        assert "\x1b[31m" in view.text

    def test_no_ansi_codes_when_disabled(self):
        view = render_workflow_status(_sample_payload(), colour=False)
        assert "\x1b" not in view.text

    def test_colour_summary(self):
        view = render_workflow_status(_sample_payload(), colour=True)
        assert "\x1b[33m" in view.summary_line


class TestRenderTextHelper:
    def test_helper_returns_text_only(self):
        text = render_workflow_status_text(_sample_payload())
        assert "Workflow for goal" in text
        assert "\x1b" not in text


class TestToDict:
    def test_view_serialises_to_dict(self):
        view = render_workflow_status(_sample_payload())
        d = view.to_dict()
        assert d["has_blocking_step"] is True
        assert d["blocking_step_count"] == 1
        assert "text" in d
        assert "summary_line" in d
