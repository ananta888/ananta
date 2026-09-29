from __future__ import annotations

from client_surfaces.operator_tui.models import OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state(payload: dict) -> OperatorState:
    return OperatorState(
        endpoint="http://localhost:5000",
        section_id="artifacts",
        panel_states={"artifacts": PanelState.HEALTHY},
        section_payloads={"artifacts": payload},
        header_logo_game={"active_goal_id": "goal-render"},
    )


def _payload() -> dict:
    return {
        "planning_track_mode": True,
        "goal_id": "goal-render",
        "planning_status": "valid",
        "planning_lifecycle": ["pending", "validating", "valid"],
        "selected_output_id": "out-1",
        "active_output_id": "out-1",
        "task_filters": {},
        "status_issues": [],
        "plan_diff": {},
        "track_rows": [
            {
                "output_artifact_id": "out-1",
                "status": "created",
                "verification_status": "valid",
                "provenance": {"provenance_id": "prov-1", "model_ref": {"model_id": "planner-test"}},
                "payload": {},
            }
        ],
        "selected_track": {
            "owner": "operator_tui",
            "track": "track-render",
            "goal": "Renderer Goal",
            "milestones": [{"id": "M01", "title": "Setup", "status": "todo", "task_ids": ["T01"]}],
            "tasks_status_summary": {"total": 1, "by_status": {"todo": 1, "done": 0}},
            "progress_summary": {"state": "todo", "count_based_percent": 0, "weighted_percent": 0},
            "weighted_progress_summary": {"blocked_weight": 0},
            "tasks_type_summary": {"by_type": {"test": {"total": 1, "done": 0, "partial": 0, "blocked": 0, "progress_percent": 0}}},
            "derived_summary_metadata": {"source_hash": "abcdef1234567890"},
            "summary_recalculation_status": "repaired",
            "repaired_fields": ["tasks_status_summary"],
            "tasks_filtered": [
                {
                    "id": "T01",
                    "title": "Render Task",
                    "status": "todo",
                    "priority": "P1",
                    "risk": "low",
                    "type": "test",
                }
            ],
            "critical_path_tasks": ["T01"],
            "quality_gate_warnings": [{"path": "tasks/0", "reason_code": "quality_p1_acceptance_not_testable"}],
            "provenance": {"provenance_id": "prov-1", "model_ref": {"model_id": "planner-test"}},
            "task_mapping": {"T01": "ptask-1"},
            "source_references": ["usage-1"],
            "context_references": ["artifact:allowed"],
        },
    }


def test_renderer_shows_planning_track_header_and_sections() -> None:
    output = render_operator_shell(_state(_payload()), width=170, height=36)
    assert "Planning Track: goal-render" in output
    assert "Status: valid" in output
    assert "Header: owner=operator_tui" in output
    assert "[Milestones]" in output
    assert "[Tasks]" in output
    assert "Critical path tasks: T01" in output
    assert "Provenance: prov-1" in output
    assert "[Quality warnings]" in output
    assert "Progress: count_based=0%" in output
    assert "Derived summary: status=repaired" in output
    assert "[Type progress]" in output


def test_renderer_shows_compact_planning_track_view_on_small_width() -> None:
    output = render_operator_shell(_state(_payload()), width=96, height=32)
    assert "compact view" in output
    assert "Selected output: out-1" in output


def test_renderer_shows_plan_diff_summary() -> None:
    payload = _payload()
    payload["plan_diff"] = {
        "left_output_id": "out-1",
        "right_output_id": "out-2",
        "new_tasks": [{"id": "T02"}],
        "changed_tasks": [{"id": "T01"}],
        "removed_tasks": [],
    }
    output = render_operator_shell(_state(payload), width=170, height=44)
    assert "[Plan diff]" in output
    assert "out-1 -> out-2" in output


def test_renderer_shows_repaired_lifecycle_and_new_commands() -> None:
    payload = _payload()
    payload["planning_status"] = "degraded"
    payload["planning_lifecycle"] = ["pending", "validating", "repaired", "degraded"]
    output = render_operator_shell(_state(payload), width=170, height=36)
    assert "repaired -> degraded" in output
