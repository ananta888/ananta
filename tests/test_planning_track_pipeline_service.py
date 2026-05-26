from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.planning_track_pipeline_service import (
    compute_tasks_status_summary,
    evaluate_planning_quality_gates,
    persist_planning_track_result,
    validate_planning_track_with_details,
    validate_summary_consistency,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fixture_payload() -> dict:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "planning_tracks" / "small_track.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _artifact_service(tmp_path: Path) -> GoalArtifactService:
    return GoalArtifactService(repository=GoalArtifactRepository(root=tmp_path))


def _create_grant(service: GoalArtifactService, goal_id: str, grant_id: str, artifact_ref: str) -> None:
    service.create_grant(
        goal_id=goal_id,
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": grant_id,
            "goal_id": goal_id,
            "artifact_ref": artifact_ref,
            "granted_by": "operator",
            "granted_at": _now_iso(),
            "allowed_usages": ["read", "quote", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:test",
        },
    )


def test_validation_service_reports_required_and_empty_acceptance_criteria() -> None:
    payload = _fixture_payload()
    payload.pop("owner", None)
    payload["tasks"][0]["acceptance_criteria"] = []
    issues = validate_planning_track_with_details(payload)
    reason_codes = {item["reason_code"] for item in issues}
    assert "missing_required_field" in reason_codes
    assert "empty_acceptance_criteria" in reason_codes


def test_summary_consistency_detects_and_repairs_mismatch() -> None:
    payload = _fixture_payload()
    payload["tasks_status_summary"]["total"] = 999
    payload["tasks_status_summary"]["by_priority"] = {"P1": 1}
    failed = validate_summary_consistency(payload, repair_mode=False)
    assert failed["valid"] is False
    repaired = validate_summary_consistency(payload, repair_mode=True)
    assert repaired["valid"] is True
    assert repaired["repaired"] is True
    assert repaired["repaired_payload"]["tasks_status_summary"] == compute_tasks_status_summary(payload)


def test_quality_gates_flag_invalid_refs_and_allow_non_blocking_warnings() -> None:
    payload = _fixture_payload()
    payload["critical_path_tasks"] = ["T01", "T404"]
    payload["milestones"][0]["task_ids"] = ["T01", "T404"]
    payload["tasks"][0]["acceptance_criteria"] = ["Looks good"]
    result = evaluate_planning_quality_gates(payload, large_goal_mode=True, small_goal_mode=False, min_tasks_large_goal=5)
    assert result["ok"] is False
    reason_codes = {item["reason_code"] for item in list(result["blocking_issues"] or [])}
    assert "quality_critical_path_missing_task" in reason_codes
    assert "quality_milestone_missing_task" in reason_codes
    warning_codes = {item["reason_code"] for item in list(result["warnings"] or [])}
    assert "quality_p1_acceptance_not_testable" in warning_codes


def test_persist_planning_track_result_saves_valid_artifact_and_provenance(tmp_path: Path) -> None:
    goal_id = "goal-planning-valid"
    service = _artifact_service(tmp_path)
    _create_grant(service, goal_id, "grant-valid", "artifact:allowed")
    payload = _fixture_payload()
    result = persist_planning_track_result(
        goal_id=goal_id,
        task_id="task-plan",
        worker_id="worker-planner",
        raw_output=json.dumps(payload),
        prompt_template_ref="prompt:planning/track_planning",
        final_prompt="rendered planning prompt",
        model_ref={"provider_id": "local", "model_id": "planner-test"},
        config_refs={
            "worker_config_ref": "cfg:worker",
            "runtime_config_ref": "cfg:runtime",
            "model_config_ref": "cfg:model",
            "policy_config_ref": "cfg:policy",
        },
        available_artifacts=[{"source_ref": "artifact:allowed"}, {"source_ref": "artifact:denied"}],
        goal_artifact_service=service,
    )
    assert result["status"] == "valid"
    assert result["output_artifact"]["artifact_type"] == "planning_track"
    assert result["output_artifact"]["status"] == "created"
    assert result["source_usage_refs"]
    assert result["denied_context_refs"] == ["artifact:denied"]
    provenance = service.get_execution_provenance(goal_id=goal_id, provenance_id=result["provenance_id"])
    assert provenance is not None
    assert provenance["prompt_refs"]["prompt_template_ref"] == "prompt:planning/track_planning"
    assert provenance["extensions"]["schema_ref"] == "todos/todo.track.schema.json"
    assert result["output_artifact"]["extensions"]["quality_gate_warnings"] == []


def test_persist_planning_track_result_repair_pipeline_runs_once_and_degrades(tmp_path: Path) -> None:
    goal_id = "goal-planning-repair"
    service = _artifact_service(tmp_path)

    repaired_payload = _fixture_payload()
    repaired_payload.pop("tasks_status_summary", None)

    def _repair_fn(_prompt: str) -> str:
        return json.dumps(repaired_payload)

    result = persist_planning_track_result(
        goal_id=goal_id,
        task_id="task-plan",
        worker_id="worker-planner",
        raw_output="this is prose only",
        prompt_template_ref="prompt:planning/track_planning",
        final_prompt="rendered planning prompt",
        model_ref={"provider_id": "local", "model_id": "planner-test"},
        config_refs={
            "worker_config_ref": "cfg:worker",
            "runtime_config_ref": "cfg:runtime",
            "model_config_ref": "cfg:model",
            "policy_config_ref": "cfg:policy",
        },
        repair_fn=_repair_fn,
        goal_artifact_service=service,
    )
    assert result["repair_attempt_count"] == 1
    assert result["status"] == "degraded"
    assert result["output_artifact"]["status"] == "failed"
    assert result["output_artifact"]["extensions"]["active_plan_candidate"] is False
