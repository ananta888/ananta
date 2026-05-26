from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.goal_artifact_filters import filter_goal_artifact_view
from client_surfaces.operator_tui.models import OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def test_goal_commands_require_active_goal() -> None:
    result = execute_command(":goal artifacts", _state())
    assert result.handled is False
    assert "active goal" in result.message


def test_goal_use_and_artifact_management_flow(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    state = _state()
    used = execute_command(":goal use goal-1", state)
    assert used.handled is True
    assert (used.state.header_logo_game or {}).get("active_goal_id") == "goal-1"
    assert used.state.section_id == "artifacts"
    assert (used.state.panel_states or {}).get("artifacts") == PanelState.HEALTHY

    granted = execute_command(":goal source grant sources:keycloak:snap_1 --usage use_as_context", used.state)
    assert granted.handled is True
    payload = json.loads(granted.message)
    grant_id = str(payload.get("grant_id") or "")
    assert grant_id.startswith("grant-")

    detail = execute_command(f":goal source detail {grant_id}", granted.state)
    detail_payload = json.loads(detail.message)
    assert detail_payload["grant_id"] == grant_id
    assert detail_payload["policy_decision_ref"]

    revoked = execute_command(f":goal source revoke {grant_id}", detail.state)
    assert revoked.handled is True
    assert "revoked" in revoked.state.status_message


def test_goal_artifacts_command_filters_and_provenance(monkeypatch, tmp_path: Path) -> None:
    from agent.artifacts.goal_artifact_service import GoalArtifactService
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    service = GoalArtifactService()
    service.create_grant(
        goal_id="goal-2",
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-2",
            "goal_id": "goal-2",
            "artifact_ref": "sources:keycloak:snap_2",
            "granted_by": "operator",
            "granted_at": "2026-05-26T00:00:00Z",
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy-2",
        },
    )
    service.record_usage(
        goal_id="goal-2",
        usage={
            "schema": "source_artifact_usage.v1",
            "usage_id": "usage-2",
            "grant_id": "grant-2",
            "goal_id": "goal-2",
            "task_id": "task-2",
            "worker_id": "worker-2",
            "artifact_ref": "sources:keycloak:snap_2",
            "usage_kind": "embedded",
            "used_at": "2026-05-26T00:00:00Z",
            "context_hash": "deadbeef1234abcd",
            "policy_decision_ref": "policy-2",
        },
    )
    service.record_output_artifact(
        goal_id="goal-2",
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-2",
            "goal_id": "goal-2",
            "task_id": "task-2",
            "worker_id": "worker-2",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": ["usage-2"],
            "artifact_ref": "artifacts:report:2",
            "content_hash": "c" * 64,
            "status": "created",
            "provenance_summary": "summary",
        },
    )
    state = execute_command(":goal use goal-2", _state()).state
    filtered = execute_command(":goal artifacts filter source_id=keycloak", state)
    assert filtered.handled is True
    assert "filters=source_id=keycloak" in filtered.state.status_message
    cleared = execute_command(":goal artifacts clear-filter", filtered.state)
    assert cleared.handled is True
    assert "filters=none" in cleared.state.status_message
    provenance = execute_command(":artifact provenance out-2", cleared.state)
    provenance_payload = json.loads(provenance.message)
    assert provenance_payload["output_artifact_id"] == "out-2"
    assert provenance_payload["sources"][0]["artifact_ref"] == "sources:keycloak:snap_2"

    output = render_operator_shell(cleared.state, width=120, height=34)
    assert "Goal Artifacts:" in output


def test_goal_artifact_filter_logic_independent_from_renderer() -> None:
    result = filter_goal_artifact_view(
        source_grants=[{"artifact_ref": "sources:keycloak:snap_a", "sensitivity": "internal"}],
        source_usages=[{"artifact_ref": "sources:wiki:snap_b", "worker_id": "w1"}],
        output_artifacts=[{"artifact_type": "report", "status": "created"}],
        filters={"source_id": "keycloak"},
    )
    assert len(result["source_grants"]) == 1
    assert len(result["source_usages"]) == 0
