from __future__ import annotations

import json
from pathlib import Path

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from client_surfaces.operator_tui.diff.ai_diff_context import build_ai_diff_context_envelope
from client_surfaces.operator_tui.diff.ai_diff_dispatch import dispatch_ai_diff_request
from client_surfaces.operator_tui.diff.ai_diff_prompts import get_ai_diff_prompt_template, render_ai_diff_prompt
from client_surfaces.operator_tui.diff.ai_diff_panel_state import build_ai_diff_panel_state
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver
from client_surfaces.operator_tui.diff.diff_sources import build_current_diff_source_ref
from client_surfaces.operator_tui.diff.three_way_diff_state import build_current_diff_three_panel_session


def _seed_goal_with_grant_and_output(tmp_path: Path) -> GoalArtifactService:
    service = GoalArtifactService(repository=GoalArtifactRepository(root=tmp_path / "store"))
    service.create_grant(
        goal_id="goal-ctx",
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-1",
            "goal_id": "goal-ctx",
            "artifact_ref": "sources:keycloak:snap_1",
            "granted_by": "operator",
            "granted_at": "2026-05-26T00:00:00Z",
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:1",
        },
    )
    service.record_output_artifact(
        goal_id="goal-ctx",
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-ctx-1",
            "goal_id": "goal-ctx",
            "task_id": "task-ctx",
            "worker_id": "worker-ctx",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": [],
            "artifact_ref": f"file:{tmp_path / 'report.txt'}",
            "content_hash": "a" * 64,
            "status": "created",
            "provenance_summary": "ctx output",
        },
    )
    return service


def test_context_envelope_contains_denied_and_truncation(tmp_path: Path) -> None:
    service = _seed_goal_with_grant_and_output(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "demo.txt").write_text("old\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.local"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "tests"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "add", "demo.txt"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)
    (repo / "demo.txt").write_text("x" * 5000, encoding="utf-8")
    session = build_current_diff_three_panel_session(session_id="s-1", goal_id="goal-ctx")
    session["panels"][0]["source_left"] = build_current_diff_source_ref()
    session["panels"][1]["source_left"] = {
        "schema": "diff_source_ref.v1",
        "source_ref_id": "src-art",
        "source_kind": "artifact_ref",
        "display_name": "Artifact",
        "locator": {"artifact_ref": "sources:unknown:snap_2"},
    }
    session["extensions"]["ai_panel_state"] = build_ai_diff_panel_state(mode="review", selected_panels=["A", "B"], selected_hunks=["hunk-1"])
    envelope = build_ai_diff_context_envelope(
        diff3_state=session,
        goal_id="goal-ctx",
        goal_artifact_service=service,
        resolver=DiffSourceResolver(repo_root=repo, goal_artifact_service=service),
        max_context_chars=200,
    )
    assert envelope["schema"] == "ai_diff_context_envelope.v1"
    assert envelope["selected_hunk_refs"] == ["hunk-1"]
    assert envelope["truncated"] is True


def test_context_envelope_marks_denied_artifact_refs(tmp_path: Path) -> None:
    service = _seed_goal_with_grant_and_output(tmp_path)
    session = build_current_diff_three_panel_session(session_id="s-1", goal_id="goal-ctx")
    session["panels"][0]["source_left"] = {
        "schema": "diff_source_ref.v1",
        "source_ref_id": "src-art",
        "source_kind": "artifact_ref",
        "display_name": "Artifact",
        "locator": {"artifact_ref": "sources:unknown:snap_2"},
    }
    session["extensions"]["ai_panel_state"] = build_ai_diff_panel_state(mode="review", selected_panels=["A"])
    envelope = build_ai_diff_context_envelope(diff3_state=session, goal_id="goal-ctx", goal_artifact_service=service)
    assert "sources:unknown:snap_2" in envelope["denied_context_refs"]


def test_prompt_templates_render_control_task_and_schema() -> None:
    template = get_ai_diff_prompt_template("patch")
    assert "control" in template and "task" in template
    rendered = render_ai_diff_prompt(mode="patch", context_envelope={"goal_id": "goal-1"})
    assert "CONTROL:" in rendered
    assert "TASK:" in rendered
    assert "DIFF_CONTEXT:" in rendered
    assert "OUTPUT_SCHEMA:" in rendered


def test_dispatch_registers_patch_output_and_provenance(tmp_path: Path, monkeypatch) -> None:
    from agent.config import settings
    from client_surfaces.operator_tui.diff import ai_diff_dispatch as dispatch_module

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    service = _seed_goal_with_grant_and_output(tmp_path)
    monkeypatch.setattr(dispatch_module, "GoalArtifactService", lambda: service)
    (tmp_path / "report.txt").write_text("artifact output", encoding="utf-8")
    session = build_current_diff_three_panel_session(session_id="s-2", goal_id="goal-ctx")
    session["extensions"]["ai_panel_state"] = build_ai_diff_panel_state(mode="patch", selected_panels=["A", "B"])
    result = dispatch_ai_diff_request(goal_id="goal-ctx", diff3_state=session, mode="patch")
    assert result["status"] == "success"
    assert result["provenance_id"]
    assert result["output_artifact_id"]
    graph = service.get_goal_graph("goal-ctx")
    output = next((item for item in graph["output_artifacts"] if item["output_artifact_id"] == result["output_artifact_id"]), None)
    assert output is not None
    assert output["artifact_type"] == "patch_suggestion"
    prov = service.get_execution_provenance(goal_id="goal-ctx", provenance_id=result["provenance_id"])
    assert prov is not None
    assert prov["prompt_refs"]["prompt_template_ref"].startswith("prompt:diff3/")
    assert prov["prompt_refs"]["final_prompt_hash"]


def test_dispatch_invalid_ai_response_becomes_degraded(tmp_path: Path, monkeypatch) -> None:
    from agent.config import settings
    from client_surfaces.operator_tui.diff import ai_diff_dispatch as dispatch_module

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data-invalid"))
    service = _seed_goal_with_grant_and_output(tmp_path)
    monkeypatch.setattr(dispatch_module, "GoalArtifactService", lambda: service)
    monkeypatch.setattr(dispatch_module, "_AI_RESPONSE_GENERATOR", lambda *, mode, envelope: {"invalid": True})
    session = build_current_diff_three_panel_session(session_id="s-invalid", goal_id="goal-ctx")
    session["extensions"]["ai_panel_state"] = build_ai_diff_panel_state(mode="review", selected_panels=["A"])
    result = dispatch_ai_diff_request(goal_id="goal-ctx", diff3_state=session, mode="review")
    assert result["status"] == "degraded"
    assert result["reason_code"] == "invalid_ai_diff_response"


def test_diff3_ai_run_command_handles_degraded_response(tmp_path: Path, monkeypatch) -> None:
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorState

    def _bad(*, goal_id: str | None, diff3_state: dict, mode: str) -> dict:
        return {"status": "degraded", "reason_code": "invalid_ai_diff_response", "response": {}, "context_envelope": {}}

    monkeypatch.setattr("client_surfaces.operator_tui.commands.dispatch_ai_diff_request", _bad)
    state = OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})
    opened = execute_command(":diff3", state)
    ran = execute_command(":diff3 ai run review", opened.state)
    assert ran.handled is True
    assert "degraded" in ran.state.status_message
    payload = json.loads(ran.message)
    assert payload["status"] == "degraded"


def test_diff3_ai_run_timeout_sets_degraded_state(monkeypatch) -> None:
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorState

    monkeypatch.setattr("client_surfaces.operator_tui.commands.dispatch_ai_diff_request", lambda **kwargs: (_ for _ in ()).throw(TimeoutError()))
    state = OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})
    opened = execute_command(":diff3", state)
    ran = execute_command(":diff3 ai run review", opened.state)
    assert ran.handled is True
    assert "degraded" in ran.state.status_message
    ai_state = ran.state.header_logo_game["diff3_state"]["extensions"]["ai_panel_state"]
    assert ai_state["status"] == "degraded"


def test_diff3_ai_run_success_stores_structured_findings(monkeypatch) -> None:
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorState

    def _ok(*, goal_id: str | None, diff3_state: dict, mode: str) -> dict:
        return {
            "status": "success",
            "reason_code": "",
            "response": {
                "schema": "ai_diff_response.v1",
                "status": "success",
                "artifact_type": mode,
                "summary": "ok",
                "findings": ["f1", "f2"],
                "risks": [],
                "suggested_tests": [],
                "patch_suggestions": [],
                "source_refs": [],
            },
            "context_envelope": {},
            "provenance_id": "prov-1",
            "output_artifact_id": "out-1",
        }

    monkeypatch.setattr("client_surfaces.operator_tui.commands.dispatch_ai_diff_request", _ok)
    state = OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})
    opened = execute_command(":diff3", state)
    ran = execute_command(":diff3 ai run review", opened.state)
    assert ran.handled is True
    payload = json.loads(ran.message)
    assert payload["response"]["findings"] == ["f1", "f2"]
    extensions = ran.state.header_logo_game["diff3_state"]["extensions"]
    assert extensions["ai_last_findings"] == ["f1", "f2"]
