from __future__ import annotations

from types import SimpleNamespace

from agent.routes.tasks.auto_planner import AutoPlanner  # noqa: F401
from agent.services import lifecycle_service


class _QueueStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def ingest_task(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


def test_materialize_from_plan_node_carries_blueprint_provenance_to_task_metadata(monkeypatch) -> None:
    queue = _QueueStub()
    monkeypatch.setattr(lifecycle_service, "get_task_queue_service", lambda: queue)

    node = SimpleNamespace(
        id="node-1",
        title="Implement feature",
        description="Implement with tests",
        priority="High",
        rationale={
            "task_kind": "coding",
            "retrieval_intent": "symbol_and_dependency_neighborhood",
            "required_context_scope": "module_and_related_symbols",
            "preferred_bundle_mode": "standard",
            "required_capabilities": ["coding", "testing"],
            "blueprint_id": "bp-1",
            "blueprint_name": "TDD",
            "blueprint_artifact_id": "artifact-1",
            "blueprint_role_name": "Implementer",
            "template_name": "TDD Implementer Template",
        },
        verification_spec={"policy": True},
    )

    lifecycle_service.TaskLifecycleService().materialize_from_plan_node(
        task_id="task-1",
        node=node,
        team_id="team-1",
        goal_id="goal-1",
        goal_trace_id="trace-1",
        plan_id="plan-1",
        parent_task_id="parent-1",
        derivation_reason="goal_template",
        derivation_depth=1,
        depends_on=["task-0"],
    )

    extra_fields = queue.calls[0]["extra_fields"]
    context = extra_fields["worker_execution_context"]
    provenance = context["planning_provenance"]
    assert provenance["blueprint_id"] == "bp-1"
    assert provenance["blueprint_name"] == "TDD"
    assert provenance["blueprint_artifact_id"] == "artifact-1"
    assert provenance["blueprint_role_name"] == "Implementer"
    assert provenance["template_name"] == "TDD Implementer Template"
    assert extra_fields["status_reason_details"]["planning_provenance"]["blueprint_id"] == "bp-1"


def test_materialize_from_plan_node_remains_backward_compatible_without_blueprint_provenance(monkeypatch) -> None:
    queue = _QueueStub()
    monkeypatch.setattr(lifecycle_service, "get_task_queue_service", lambda: queue)

    node = SimpleNamespace(
        id="node-2",
        title="General task",
        description="No blueprint metadata",
        priority="Medium",
        rationale={
            "task_kind": "coding",
            "required_capabilities": ["coding"],
        },
        verification_spec={},
    )

    lifecycle_service.TaskLifecycleService().materialize_from_plan_node(
        task_id="task-2",
        node=node,
        team_id=None,
        goal_id=None,
        goal_trace_id=None,
        plan_id=None,
        parent_task_id=None,
        derivation_reason="goal_planning",
        derivation_depth=0,
        depends_on=None,
    )

    extra_fields = queue.calls[0]["extra_fields"]
    provenance = extra_fields["worker_execution_context"]["planning_provenance"]
    assert provenance["plan_node_id"] == "node-2"
    assert "blueprint_id" not in provenance
