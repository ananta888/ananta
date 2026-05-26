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
            "provenance_id": "prov-2",
            "provenance_kind": "worker_execution",
        },
    )
    service.upsert_execution_provenance(
        goal_id="goal-2",
        provenance={
            "schema": "execution_provenance.v1",
            "provenance_id": "prov-2",
            "goal_id": "goal-2",
            "task_id": "task-2",
            "execution_id": "exec-2",
            "worker_id": "worker-2",
            "worker_kind": "native",
            "runtime_target_ref": {"runtime_type": "ananta-worker", "location": "local"},
            "model_ref": {"provider_id": "local", "model_id": "none"},
            "config_refs": {
                "worker_config_ref": "cfg-worker-2",
                "runtime_config_ref": "cfg-runtime-2",
                "model_config_ref": "cfg-model-2",
                "policy_config_ref": "cfg-policy-2",
            },
            "prompt_refs": {
                "prompt_template_ref": "prompt:goal-2",
                "prompt_template_version": "v1",
                "prompt_template_hash": "a" * 64,
                "prompt_variables_hash": "b" * 64,
                "final_prompt_hash": "c" * 64,
                "raw_prompt_stored": False,
            },
            "input_usage_refs": ["usage-2"],
            "output_artifact_refs": ["out-2"],
            "created_at": "2026-05-26T00:00:00Z",
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
    assert provenance_payload["runtime_target_ref"]["runtime_type"] == "ananta-worker"
    assert provenance_payload["prompt_refs"]["prompt_template_ref"] == "prompt:goal-2"
    prompt_detail = execute_command(":artifact prompt out-2", cleared.state)
    prompt_payload = json.loads(prompt_detail.message)
    assert prompt_payload["prompt_template_ref"] == "prompt:goal-2"
    assert prompt_payload["raw_prompt_status"] == "raw prompt not stored"
    config_detail = execute_command(":artifact config out-2", cleared.state)
    config_payload = json.loads(config_detail.message)
    assert config_payload["worker_config_ref"] == "cfg-worker-2"
    payload = json.loads(cleared.message)
    assert payload["output_artifacts"][0]["execution_summary"]

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


def test_ai_explain_artifact_graph_requires_active_goal() -> None:
    result = execute_command(":ai explain artifact-graph", _state())
    assert result.handled is False
    assert "requires active goal" in result.message


def test_ai_explain_artifact_graph_shows_counts_without_denied_refs(monkeypatch, tmp_path: Path) -> None:
    from agent.artifacts.goal_artifact_service import GoalArtifactService
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    service = GoalArtifactService()
    goal_id = "goal-explain"
    service.create_grant(
        goal_id=goal_id,
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-explain",
            "goal_id": goal_id,
            "artifact_ref": "sources:keycloak:snap_9",
            "granted_by": "operator",
            "granted_at": "2026-05-26T00:00:00Z",
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:explain",
        },
    )
    tracking = service.validate_and_record_context_usages(
        goal_id=goal_id,
        artifact_refs=["sources:keycloak:snap_9", "sources:wikipedia:snap_404"],
        task_id="task-explain",
        worker_id="worker-explain",
        context_hash="ctx-explain-1",
    )
    service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-explain",
            "goal_id": goal_id,
            "task_id": "task-explain",
            "worker_id": "worker-explain",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": tracking["source_usage_refs"],
            "artifact_ref": "artifacts:report:explain",
            "content_hash": "f" * 64,
            "status": "created",
            "provenance_summary": "explain",
        },
    )

    state = execute_command(f":goal use {goal_id}", _state()).state
    explained = execute_command(":ai explain artifact-graph", state)
    assert explained.handled is True
    assert "Freigegeben: 1" in explained.message
    assert "Genutzt: 1" in explained.message
    assert "Erzeugt: 1" in explained.message
    assert "wikipedia" not in explained.message
