from __future__ import annotations

from client_surfaces.operator_tui.models import OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state(payload: dict) -> OperatorState:
    return OperatorState(
        endpoint="http://localhost:5000",
        section_id="artifacts",
        panel_states={"artifacts": PanelState.HEALTHY},
        section_payloads={"artifacts": payload},
        header_logo_game={"active_goal_id": "goal-r"},
    )


def _payload() -> dict:
    return {
        "goal_artifacts_mode": True,
        "goal_id": "goal-r",
        "filters": {},
        "source_grants": [],
        "source_usages": [],
        "output_artifacts": [],
    }


def test_renderer_goal_artifacts_empty_graph() -> None:
    output = render_operator_shell(_state(_payload()), width=120, height=34)
    assert "Goal Artifacts: goal-r" in output
    assert "compact view" in output
    assert "(empty goal artifact graph)" in output


def test_renderer_goal_artifacts_only_grants_and_used_unused_markers() -> None:
    payload = _payload()
    payload["source_grants"] = [
        {"grant_id": "grant-used", "artifact_ref": "sources:keycloak:s1", "sensitivity": "internal", "data_boundary": "project_private"},
        {"grant_id": "grant-unused", "artifact_ref": "sources:wiki:s2", "sensitivity": "internal", "data_boundary": "project_private"},
    ]
    payload["source_usages"] = [
        {"usage_id": "usage-1", "grant_id": "grant-used", "task_id": "task-1", "worker_id": "worker-1", "artifact_ref": "sources:keycloak:s1"},
    ]
    output = render_operator_shell(_state(payload), width=176, height=34)
    assert "grant-used [used]" in output
    assert "grant-unused [granted-not-used]" in output


def test_renderer_goal_artifacts_grants_usage_outputs_small_width_compact() -> None:
    payload = _payload()
    payload["source_grants"] = [
        {"grant_id": "grant-1", "artifact_ref": "sources:keycloak:s1", "sensitivity": "internal", "data_boundary": "project_private"},
    ]
    payload["source_usages"] = [
        {"usage_id": "usage-1", "grant_id": "grant-1", "task_id": "task-1", "worker_id": "worker-1", "artifact_ref": "sources:keycloak:s1"},
    ]
    payload["output_artifacts"] = [
        {
            "output_artifact_id": "out-1",
            "artifact_type": "report",
            "status": "created",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "created_at": "2026-05-26T00:00:00Z",
        }
    ]
    output = render_operator_shell(_state(payload), width=110, height=30)
    assert "compact view" in output
    assert "grant grant-1" in output
    assert "usage usage-1" in output
    assert "output out-1" in output


def test_renderer_goal_artifacts_strips_ansi_escape_from_data_fields() -> None:
    payload = _payload()
    payload["source_grants"] = [
        {
            "grant_id": "grant-1",
            "artifact_ref": "sources:\x1b[31mkeycloak\x1b[0m:s1",
            "sensitivity": "internal",
            "data_boundary": "project_private",
        }
    ]
    output = render_operator_shell(_state(payload), width=120, height=32)
    assert "sources:keycloak:s1" in output
    assert "sources:\x1b[31mkeycloak\x1b[0m:s1" not in output
