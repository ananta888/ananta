from __future__ import annotations

import json
from pathlib import Path

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.repository import task_repo
from agent.services.planning_track_pipeline_service import persist_planning_track_result
from agent.services.planning_track_task_integration_service import (
    PlanningTrackTaskIntegrationService,
    _extract_workflow_step,
    _task_id_for_plan_task,
)


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


# --- WFG-007: workflow step materialization -------------------------------

def _enrich_fixture_with_workflow() -> dict:
    """Return a copy of the base fixture with workflow step annotations
    attached to each plan task, simulating the output of
    BlueprintPlanningAdapter (WFG-006)."""
    payload = _fixture_payload()
    workflow_meta = {
        "T01": {
            "blueprint_workflow_step_id": "step-1",
            "blueprint_workflow_step_id_label": "intake",
            "blueprint_role_name": "product_owner",
            "task_kind": "planning",
            "gate": False,
            "checks": {},
            "failure_policy": None,
            "required_capabilities": ["intake.capture"],
            "produces": ["goal_brief", "acceptance_criteria"],
            "consumes": [],
        },
        "T02": {
            "blueprint_workflow_step_id": "step-2",
            "blueprint_workflow_step_id_label": "planning",
            "blueprint_role_name": "planner",
            "task_kind": "planning",
            "gate": False,
            "checks": {},
            "failure_policy": None,
            "required_capabilities": ["plan.write"],
            "produces": ["execution_plan"],
            "consumes": ["goal_brief"],
        },
        "T03": {
            "blueprint_workflow_step_id": "step-3",
            "blueprint_workflow_step_id_label": "planning_review",
            "blueprint_role_name": "scrum_master",
            "task_kind": "gate_review",
            "gate": True,
            "checks": {"plan_has_small_tasks": {}, "deps_acyclic": {}},
            "failure_policy": "block",
            "required_capabilities": ["gate.review"],
            "produces": ["planning_gate_decision"],
            "consumes": ["execution_plan", "acceptance_criteria"],
        },
    }
    for task in list(payload.get("tasks") or []):
        plan_id = str(task.get("id") or "")
        meta = workflow_meta.get(plan_id)
        if meta:
            task.update(meta)
    # T02 depends on T01; T03 depends on T02 to make the gate block.
    for task in list(payload.get("tasks") or []):
        plan_id = str(task.get("id") or "")
        if plan_id == "T02":
            task["depends_on"] = ["T01"]
        elif plan_id == "T03":
            task["depends_on"] = ["T02"]
    return payload


def _persist_track_with_workflow(service: GoalArtifactService, goal_id: str) -> str:
    _create_grant(service, goal_id, "grant-track-wf", "artifact:allowed")
    payload = _enrich_fixture_with_workflow()
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


def test_extract_workflow_step_returns_empty_for_legacy_task() -> None:
    """A plan task without blueprint_workflow_step_id is a legacy
    task; no workflow_step block should be produced."""
    assert _extract_workflow_step({}) == {}
    assert _extract_workflow_step({"id": "T1", "title": "old"}) == {}


def test_extract_workflow_step_normalizes_adapter_fields() -> None:
    plan_task = {
        "id": "T03",
        "blueprint_workflow_step_id": "step-3",
        "blueprint_workflow_step_id_label": "planning_review",
        "blueprint_role_name": "scrum_master",
        "task_kind": "gate_review",
        "gate": True,
        "checks": {"plan_has_small_tasks": {}},
        "failure_policy": "block",
        "required_capabilities": ["gate.review"],
        "produces": ["decision"],
        "consumes": ["plan"],
    }
    step = _extract_workflow_step(plan_task)
    assert step["schema"] == "workflow_step_provenance.v1"
    assert step["step_id"] == "step-3"
    assert step["step_label"] == "planning_review"
    assert step["role"] == "scrum_master"
    assert step["task_kind"] == "gate_review"
    assert step["gate"] is True
    assert step["checks"] == {"plan_has_small_tasks": {}}
    assert step["failure_policy"] == "block"
    assert step["required_capabilities"] == ["gate.review"]


def test_materialize_tasks_persists_workflow_step_provenance(
    monkeypatch, tmp_path: Path
) -> None:
    """WFG-007: when a plan task carries blueprint workflow annotations
    the materializer must persist them as a stable workflow_step block
    on the internal task's worker_execution_context."""
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    goal_id = "goal-track-wf-1"
    service = _artifact_service(tmp_path)
    output_id = _persist_track_with_workflow(service, goal_id)
    integration = PlanningTrackTaskIntegrationService(goal_artifact_service=service)

    materialized = integration.materialize_tasks(
        goal_id=goal_id, output_artifact_id=output_id
    )
    mapping = dict(materialized.get("plan_task_to_internal_task") or {})

    # Every plan task is materialized exactly once (idempotency baseline).
    assert len(mapping) == 3
    materialized_ids = set(materialized["materialized_task_ids"])
    assert len(materialized_ids) == 3

    # The gate task (T03) must carry the workflow_step block with checks.
    t03_internal = mapping["T03"]
    gate_task = task_repo.get_by_id(t03_internal)
    assert gate_task is not None
    wf_ctx = dict(gate_task.worker_execution_context or {})
    assert "workflow_step" in wf_ctx
    step = wf_ctx["workflow_step"]
    assert step["step_id"] == "step-3"
    assert step["role"] == "scrum_master"
    assert step["task_kind"] == "gate_review"
    assert step["gate"] is True
    assert "plan_has_small_tasks" in step["checks"]

    # The non-gate planner task also carries its own provenance.
    t02_internal = mapping["T02"]
    planner_task = task_repo.get_by_id(t02_internal)
    planner_step = dict(planner_task.worker_execution_context or {}).get("workflow_step")
    assert planner_step is not None
    assert planner_step["step_id"] == "step-2"
    assert planner_step["task_kind"] == "planning"

    # The required_capabilities from the workflow step land on the task.
    assert "plan.write" in list(planner_task.required_capabilities or [])
    assert "gate.review" in list(gate_task.required_capabilities or [])

    # Gate checks surface on verification_spec.
    assert dict(gate_task.verification_spec or {}).get("schema") == "workflow_gate_checks.v1"


def test_materialize_tasks_gate_remains_blocked_initially(
    monkeypatch, tmp_path: Path
) -> None:
    """Gate tasks must be materialized as 'blocked' so the queue
    does not pick them up before their depends_on are satisfied."""
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    goal_id = "goal-track-wf-2"
    service = _artifact_service(tmp_path)
    output_id = _persist_track_with_workflow(service, goal_id)
    integration = PlanningTrackTaskIntegrationService(goal_artifact_service=service)

    materialized = integration.materialize_tasks(
        goal_id=goal_id, output_artifact_id=output_id
    )
    mapping = dict(materialized.get("plan_task_to_internal_task") or {})

    gate_task = task_repo.get_by_id(mapping["T03"])
    planner_task = task_repo.get_by_id(mapping["T02"])
    intake_task = task_repo.get_by_id(mapping["T01"])
    # Gate must be blocked; non-gate todo tasks stay as 'todo'.
    assert str(gate_task.status) == "blocked"
    assert str(planner_task.status) == "todo"
    assert str(intake_task.status) == "todo"

    # depends_on must reference internal task ids, not plan ids, so the
    # existing queue-mechanic (WFG-005/006) is reused unchanged.
    planner_internal = _task_id_for_plan_task(
        goal_id=goal_id, output_artifact_id=output_id, plan_task_id="T02"
    )
    assert planner_internal in list(gate_task.depends_on or [])


def test_materialize_tasks_idempotent_for_workflow_steps(
    monkeypatch, tmp_path: Path
) -> None:
    """Re-running materialize_tasks on the same output must not
    duplicate internal tasks and must not change the workflow_step
    provenance already persisted on them."""
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    goal_id = "goal-track-wf-3"
    service = _artifact_service(tmp_path)
    output_id = _persist_track_with_workflow(service, goal_id)
    integration = PlanningTrackTaskIntegrationService(goal_artifact_service=service)

    first = integration.materialize_tasks(goal_id=goal_id, output_artifact_id=output_id)
    first_ids = list(first["materialized_task_ids"])

    # Second pass on the same track.
    second = integration.materialize_tasks(goal_id=goal_id, output_artifact_id=output_id)
    second_ids = list(second["materialized_task_ids"])

    assert set(first_ids) == set(second_ids)
    assert len(first_ids) == 3
    assert len(second_ids) == 3

    # Provenance stable: step_id on a previously-materialized task is
    # still the same string after re-materialization.
    for plan_task_id, internal_id in dict(second["plan_task_to_internal_task"]).items():
        task = task_repo.get_by_id(internal_id)
        step = dict(task.worker_execution_context or {}).get("workflow_step")
        assert step is not None
        assert step["step_id"].startswith("step-")
        assert step["role"] in {"product_owner", "planner", "scrum_master"}


def test_materialize_tasks_backward_compatible_for_legacy_track(
    monkeypatch, tmp_path: Path
) -> None:
    """Tracks produced without blueprint workflow annotations
    (legacy/manual) must continue to materialize without a
    workflow_step block."""
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    goal_id = "goal-track-legacy"
    service = _artifact_service(tmp_path)
    output_id = _persist_track(service, goal_id)
    integration = PlanningTrackTaskIntegrationService(goal_artifact_service=service)

    materialized = integration.materialize_tasks(
        goal_id=goal_id, output_artifact_id=output_id
    )
    for internal_id in materialized["materialized_task_ids"]:
        task = task_repo.get_by_id(internal_id)
        assert task is not None
        wf_ctx = dict(task.worker_execution_context or {})
        assert "workflow_step" not in wf_ctx
        # Planning-track provenance is still there.
        assert "planning_track" in wf_ctx
