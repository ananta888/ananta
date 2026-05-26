from __future__ import annotations

import json
from pathlib import Path

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.repository import task_repo
from agent.services.planning_track_pipeline_service import persist_planning_track_result
from agent.services.planning_track_task_integration_service import PlanningTrackTaskIntegrationService


def _fixture_payload() -> dict:
    fixture = Path(__file__).resolve().parent / "fixtures" / "planning_tracks" / "small_track.json"
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
            "granted_at": "2025-01-01T00:00:00Z",
            "allowed_usages": ["read", "quote", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:test",
        },
    )


def _persist_track(service: GoalArtifactService, goal_id: str) -> str:
    _create_grant(service, goal_id, "grant-track", "artifact:allowed")
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
        available_artifacts=[{"source_ref": "artifact:allowed"}],
        goal_artifact_service=service,
    )
    return str(result["output_artifact"]["output_artifact_id"])


def test_planning_track_materialization_and_execution_sync(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    goal_id = "goal-track-integration-1"
    service = _artifact_service(tmp_path)
    output_id = _persist_track(service, goal_id)
    integration = PlanningTrackTaskIntegrationService(goal_artifact_service=service)

    adopted = integration.adopt_track(goal_id=goal_id, output_artifact_id=output_id)
    mapping = dict(adopted.get("plan_task_to_internal_task") or {})
    assert mapping
    first_plan_task_id, first_internal_task_id = next(iter(mapping.items()))
    internal_task = task_repo.get_by_id(first_internal_task_id)
    assert internal_task is not None
    assert str(internal_task.plan_id) == output_id
    assert str(internal_task.plan_node_id) == first_plan_task_id
    assert "planning_track" in dict(internal_task.worker_execution_context or {})

    execution = integration.execute_next_plan_task(goal_id=goal_id, output_artifact_id=output_id, worker_id="worker-1")
    assert execution["internal_task_id"]
    assert execution["plan_task_id"]
    started_task = task_repo.get_by_id(str(execution["internal_task_id"]))
    assert started_task is not None
    assert str(started_task.status) == "in_progress"

    sync_result = integration.sync_plan_status_from_internal_task(
        goal_id=goal_id,
        output_artifact_id=output_id,
        plan_task_id=str(execution["plan_task_id"]),
        internal_status="completed",
    )
    assert sync_result["status"] == "done"
    graph = service.get_goal_graph(goal_id)
    row = next(
        (
            dict(item)
            for item in list(graph.get("output_artifacts") or [])
            if isinstance(item, dict) and str(item.get("output_artifact_id") or "") == output_id
        ),
        {},
    )
    payload = dict(dict(row.get("extensions") or {}).get("payload") or {})
    task_row = next((dict(item) for item in list(payload.get("tasks") or []) if str(item.get("id") or "") == str(execution["plan_task_id"])), {})
    assert task_row.get("status") == "done"
