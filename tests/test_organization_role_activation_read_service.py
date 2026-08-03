from __future__ import annotations

import json

from agent.db_models import TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)
from agent.services.organization_role_activation_read_service import (
    OrganizationRoleActivationReadService,
)


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _Session:
    def __init__(self, rows_by_entity):
        self._rows_by_entity = rows_by_entity
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def exec(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        return _Rows(self._rows_by_entity.get(entity, ()))


def _organization() -> OrganizationInstanceDB:
    return OrganizationInstanceDB(
        organization_id="organization-1",
        tenant_id="tenant-1",
        project_id="project-1",
        name="Small Company",
        definition_key="small-company",
        definition_version=1,
        definition_revision="d" * 64,
        lifecycle="active",
        effective_limit_profile_ref="organization-limits@1",
        effective_limit_profile_revision=1,
        effective_limit_profile_hash="l" * 64,
        composition_mode="custom",
        plan_digest="p" * 64,
        idempotency_key="organization-create-1",
    )


def _unit(*, tenant_id: str = "tenant-1") -> OrganizationUnitDB:
    return OrganizationUnitDB(
        id=f"unit-{tenant_id}",
        tenant_id=tenant_id,
        project_id="project-1",
        organization_id="organization-1",
        unit_key="product-team",
        name="Product Team",
        unit_kind="team",
        team_blueprint_key="small_product_team",
        team_blueprint_version=1,
        lifecycle="active",
    )


def _slot(
    slot_id: str,
    role_template_key: str,
    *,
    tenant_id: str = "tenant-1",
) -> OrganizationRoleSlotDB:
    return OrganizationRoleSlotDB(
        id=slot_id,
        tenant_id=tenant_id,
        project_id="project-1",
        organization_id="organization-1",
        unit_id=f"unit-{tenant_id}",
        slot_key=role_template_key,
        role_template_key=role_template_key,
        role_template_version=1,
        required=True,
        min_count=1,
        default_count=1,
        max_count=1,
        lifecycle="active",
    )


def _assignment(slot_id: str, agent_url: str) -> OrganizationRoleAssignmentDB:
    return OrganizationRoleAssignmentDB(
        id=f"assignment-{slot_id}",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        role_slot_id=slot_id,
        agent_url=agent_url,
        lifecycle="active",
    )


def _snapshot() -> OrganizationTopologySnapshotDB:
    return OrganizationTopologySnapshotDB(
        id="snapshot-1",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        revision=3,
        definition_revision="d" * 64,
        snapshot_hash="s" * 64,
        snapshot_json={},
    )


def _team_definition() -> TeamBlueprintRevisionDB:
    return TeamBlueprintRevisionDB(
        id="team-definition-1",
        tenant_id="tenant-1",
        project_id="project-1",
        definition_key="small_product_team",
        version=1,
        lifecycle="active",
        content_hash="t" * 64,
        workflow_definition_key="small_delivery_workflow",
        workflow_definition_version=1,
        definition_json={
            "key": "small_product_team",
            "version": 1,
            "workflow_ref": "small_delivery_workflow@1",
        },
    )


def _workflow_definition() -> WorkflowDefinitionRevisionDB:
    selector = {
        "team_blueprint_ref": "small_product_team@1",
        "cardinality": 1,
        "routing": "single",
    }
    return WorkflowDefinitionRevisionDB(
        id="workflow-definition-1",
        tenant_id="tenant-1",
        project_id="project-1",
        definition_key="small_delivery_workflow",
        version=1,
        lifecycle="active",
        content_hash="w" * 64,
        mode="strict_gated",
        default_failure_policy="block",
        steps_json=[
            {
                "step_id": "prepare",
                "title": "Prepare the work",
                "task_kind": "planning",
                "owner_role_ref": "team_lead@1",
                "target_team_selector": selector,
                "depends_on": [],
                "inputs": ["company_goal"],
                "outputs": ["implementation_brief"],
                "gate": {
                    "required": False,
                    "acceptance_checks": [],
                    "approval_role_ref": None,
                    "independent_principal_required": False,
                },
                "failure_policy": "block",
            },
            {
                "step_id": "implement",
                "title": "Implement the slice",
                "task_kind": "coding",
                "owner_role_ref": "developer@1",
                "target_team_selector": selector,
                "depends_on": ["prepare"],
                "inputs": ["implementation_brief", "acceptance_criteria"],
                "outputs": ["verified_increment", "company_goal"],
                "gate": {
                    "required": True,
                    "acceptance_checks": ["tests_passed"],
                    "approval_role_ref": "team_lead@1",
                    "independent_principal_required": True,
                },
                "failure_policy": "manual",
            },
        ],
    )


def _runtime_task(
    *,
    task_id: str,
    step_id: str,
    role_slot_id: str,
    gate: dict,
    status: str,
    depends_on: list[str] | None = None,
    worker_job_id: str | None = None,
) -> TaskDB:
    workflow_binding = {
        "schema": "organization_workflow_step_binding.v1",
        "organization_id": "organization-1",
        "definition_revision": "d" * 64,
        "workflow_ref": "small_delivery_workflow@1",
        "workflow_content_hash": "w" * 64,
        "step_id": step_id,
        "team_unit_id": "unit-tenant-1",
        "team_id": "team-runtime",
        "role_slot_id": role_slot_id,
        "gate": gate,
        "handoff_ref": None,
        "failure_policy": "block" if step_id == "prepare" else "manual",
    }
    worker_context = {"organization_workflow_step_binding": workflow_binding}
    assigned_agent_url = None
    if worker_job_id:
        assigned_agent_url = "https://secret-runtime-worker.internal"
        worker_context["planning_dispatch"] = {
            "schema": "organization_planning_dispatch.v1",
            "dispatch_intent_id": "dispatch-implement",
            "lease_id": "dispatch-lease-implement",
            "attempt": 1,
            "track_revision_id": "track-revision",
            "plan_task_id": "implement",
            "status": "dispatched",
            "assignment_id": "assignment-developer",
            "worker_job_id": worker_job_id,
            "worker_id": assigned_agent_url,
        }
    return TaskDB(
        id=task_id,
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        unit_id="unit-tenant-1",
        team_id="team-runtime",
        role_slot_id=role_slot_id,
        status=status,
        depends_on=list(depends_on or []),
        assigned_agent_url=assigned_agent_url,
        current_worker_job_id=worker_job_id,
        worker_execution_context=worker_context,
        verification_spec={
            "acceptance_checks": list(gate["acceptance_checks"]),
            "approval_role_ref": gate["approval_role_ref"],
            "independent_principal_required": gate["independent_principal_required"],
            "failure_policy": workflow_binding["failure_policy"],
        },
    )


def _unknown_runtime() -> dict:
    fact = {
        "state": "unknown",
        "reason_code": "organization_role_activation_unknown",
        "observed_true_count": 0,
        "observed_false_count": 0,
        "unknown_count": 1,
    }
    return {
        "binding": {
            "state": "unknown",
            "reason_code": "organization_role_activation_exact_task_binding_missing",
            "task_ids": [],
        },
        "task_ready": fact,
        "hub_routed": fact,
        "worker_executing": fact,
        "worker_job_count": 0,
        "active_lease_count": 0,
    }


def test_read_model_resolves_definition_edges_and_safe_assignment_coverage() -> None:
    session = _Session(
        {
            OrganizationUnitDB: [_unit(), _unit(tenant_id="foreign-tenant")],
            OrganizationRoleSlotDB: [
                _slot("slot-lead", "team_lead"),
                _slot("slot-developer", "developer"),
                _slot("slot-foreign", "developer", tenant_id="foreign-tenant"),
            ],
            OrganizationRoleAssignmentDB: [
                _assignment("slot-lead", "https://secret-agent-a.internal"),
                _assignment("slot-developer", "https://secret-agent-b.internal"),
            ],
            OrganizationTopologySnapshotDB: [_snapshot()],
            TeamBlueprintRevisionDB: [_team_definition()],
            WorkflowDefinitionRevisionDB: [_workflow_definition()],
        }
    )
    service = OrganizationRoleActivationReadService(
        catalog=object(),
        session_factory=lambda: session,
    )

    result = service.read(
        tenant_id="tenant-1",
        project_id="project-1",
        organization=_organization(),
    )

    assert result["schema"] == "organization_role_activation_map.v1"
    assert result["definition_revision"] == "d" * 64
    assert result["snapshot_hash"] == "s" * 64
    assert result["snapshot_revision"] == 3
    assert result["stale"] is False
    assert result["snapshot_reason_code"] == "organization_role_activation_snapshot_current"
    assert result["router_owner"] == "hub"
    assert result["runtime_observation"] == {
        "state": "not_observed",
        "reason_code": "organization_role_activation_exact_task_binding_missing",
        "task_state_included": False,
    }
    assert result["summary"] == {
        "active_team_count": 1,
        "workflow_step_count": 2,
        "edge_count": 3,
        "unbound_step_count": 0,
        "runtime_bound_step_count": 0,
        "task_ready_step_count": 0,
        "hub_routed_step_count": 0,
        "worker_executing_step_count": 0,
    }

    team = result["teams"][0]
    assert team["team_unit_id"] == "unit-tenant-1"
    assert team["team_blueprint_ref"] == "small_product_team@1"
    assert team["revision_binding"] == {
        "team_blueprint_content_hash": "t" * 64,
        "workflow_content_hash": "w" * 64,
    }
    prepare, implement = team["workflow"]["steps"]
    assert prepare["activation"] == {
        "state": "not_observed",
        "reason_code": "organization_role_activation_runtime_not_observed",
        "router_owner": "hub",
        "rule": "hub_route_on_workflow_start",
        "reacts_to": [
            {
                "kind": "hub_workflow_intake",
                "source_ref": "hub",
                "source_owner_role_ref": None,
            }
        ],
        "external_inputs": ["company_goal"],
        "runtime": _unknown_runtime(),
    }
    assert implement["activation"]["rule"] == "hub_route_after_dependencies"
    assert implement["activation"]["reacts_to"] == [
        {
            "kind": "workflow_step_completion",
            "source_ref": prepare["step_ref"],
            "source_owner_role_ref": "team_lead@1",
        }
    ]
    assert implement["activation"]["external_inputs"] == ["acceptance_criteria"]
    assert implement["role_binding"] == {
        "state": "bound",
        "reason_code": "organization_role_activation_owner_role_bound",
        "owner_role_ref": "developer@1",
        "candidate_role_slot_ids": ["slot-developer"],
        "bound_role_slot_ids": ["slot-developer"],
        "assignment_coverage": {
            "state": "desired_covered",
            "reason_code": "organization_role_activation_assignment_desired_covered",
            "required_count": 1,
            "desired_count": 1,
            "active_count": 1,
        },
    }
    assert {edge["type"] for edge in result["edges"]} == {
        "produces_input",
        "requires_gate",
        "unblocks",
    }
    gate_edge = next(edge for edge in result["edges"] if edge["type"] == "requires_gate")
    assert gate_edge["target"] == {
        "kind": "role_template",
        "ref": "team_lead@1",
    }
    assert gate_edge["reason_code"] == "organization_workflow_gate_declared"

    serialized = json.dumps(result)
    assert "agent_url" not in serialized
    assert "secret-agent" not in serialized


def test_runtime_projection_uses_only_exact_task_job_and_live_lease_facts() -> None:
    no_gate = {
        "required": False,
        "acceptance_checks": [],
        "approval_role_ref": None,
        "independent_principal_required": False,
    }
    approval_gate = {
        "required": True,
        "acceptance_checks": ["tests_passed"],
        "approval_role_ref": "team_lead@1",
        "independent_principal_required": True,
    }
    prepare = _runtime_task(
        task_id="task-prepare",
        step_id="prepare",
        role_slot_id="slot-lead",
        gate=no_gate,
        status="completed",
    )
    implement = _runtime_task(
        task_id="task-implement",
        step_id="implement",
        role_slot_id="slot-developer",
        gate=approval_gate,
        status="in_progress",
        depends_on=[prepare.id],
        worker_job_id="job-implement",
    )
    job = WorkerJobDB(
        id="job-implement",
        parent_task_id=implement.id,
        subtask_id="assignment-developer",
        worker_url="https://secret-runtime-worker.internal",
        status="running",
        started_at=90.0,
        slot_lease_id="lease-implement",
    )
    lease = WorkerSlotLeaseDB(
        id="lease-implement",
        status="active",
        parent_task_id=implement.id,
        worker_job_id=job.id,
        deadline_at=200.0,
    )
    foreign_job = WorkerJobDB(
        id="job-foreign",
        parent_task_id="task-foreign",
        worker_url="https://foreign-worker.internal",
        status="running",
        started_at=90.0,
    )
    session = _Session(
        {
            OrganizationUnitDB: [_unit()],
            OrganizationRoleSlotDB: [
                _slot("slot-lead", "team_lead"),
                _slot("slot-developer", "developer"),
            ],
            OrganizationRoleAssignmentDB: [],
            OrganizationRelationDB: [],
            OrganizationTopologySnapshotDB: [_snapshot()],
            TaskDB: [prepare, implement],
            WorkerJobDB: [job, foreign_job],
            WorkerSlotLeaseDB: [lease],
            TeamBlueprintRevisionDB: [_team_definition()],
            WorkflowDefinitionRevisionDB: [_workflow_definition()],
        }
    )
    service = OrganizationRoleActivationReadService(
        catalog=object(),
        session_factory=lambda: session,
        clock=lambda: 100.0,
    )

    result = service.read(
        tenant_id="tenant-1",
        project_id="project-1",
        organization=_organization(),
    )

    assert result["runtime_observation"] == {
        "state": "observed",
        "reason_code": "organization_role_activation_runtime_observed",
        "task_state_included": True,
    }
    assert result["summary"]["runtime_bound_step_count"] == 2
    assert result["summary"]["hub_routed_step_count"] == 1
    assert result["summary"]["worker_executing_step_count"] == 1
    prepare_runtime = result["teams"][0]["workflow"]["steps"][0]["activation"]["runtime"]
    assert prepare_runtime["task_ready"]["state"] == "observed_false"
    implement_runtime = result["teams"][0]["workflow"]["steps"][1]["activation"]["runtime"]
    assert implement_runtime["binding"]["task_ids"] == ["task-implement"]
    assert implement_runtime["hub_routed"]["state"] == "observed_true"
    assert implement_runtime["worker_executing"]["state"] == "observed_true"
    assert implement_runtime["worker_job_count"] == 1
    assert implement_runtime["active_lease_count"] == 1
    serialized = json.dumps(result)
    assert "secret-runtime-worker" not in serialized
    assert "foreign-worker" not in serialized


def test_task_ready_is_unknown_when_a_dependency_is_outside_the_scoped_task_set() -> None:
    task = TaskDB(id="task", status="todo", depends_on=["foreign-dependency"])

    assert OrganizationRoleActivationReadService._task_ready_fact(task, tasks_by_id={task.id: task}) == "unknown"


def test_dependency_blocked_task_is_ready_after_all_scoped_dependencies_complete() -> None:
    dependency = TaskDB(id="dependency", status="completed")
    task = TaskDB(
        id="task",
        status="blocked_by_dependency",
        depends_on=[dependency.id],
    )
    assert (
        OrganizationRoleActivationReadService._task_ready_fact(
            task,
            tasks_by_id={dependency.id: dependency, task.id: task},
        )
        == "observed_true"
    )


def test_hub_routed_requires_complete_hub_dispatch_binding() -> None:
    task = TaskDB(
        id="task",
        status="assigned",
        assigned_agent_url="worker-1",
        worker_execution_context={
            "planning_dispatch": {
                "schema": "organization_planning_dispatch.v1",
                "status": "pending_dispatch",
            }
        },
    )

    assert OrganizationRoleActivationReadService._hub_routed_fact(task) == "unknown"


def test_cross_team_handoffs_explain_declared_input_producers_without_runtime_claims() -> None:
    service = OrganizationRoleActivationReadService(catalog=object())
    source_step = {
        "step_ref": "team:direction/workflow:direction@1/step:goal",
        "owner_role_ref": "portfolio_product_owner@1",
        "outputs": ["company_goal", "accepted_requirements"],
        "handoff_ref": "lean_direction_goal_handoff@1",
        "activation": {"external_inputs": ["goal_request"]},
    }
    target_step = {
        "step_ref": "team:delivery/workflow:delivery@1/step:plan",
        "owner_role_ref": "scrum_product_owner@1",
        "outputs": ["delivery_plan"],
        "handoff_ref": None,
        "activation": {"external_inputs": ["company_goal", "accepted_requirements"]},
    }
    teams = [
        {"team_unit_id": "direction", "workflow": {"steps": [source_step]}},
        {"team_unit_id": "delivery", "workflow": {"steps": [target_step]}},
    ]
    direction_unit = _unit()
    direction_unit.id = "direction"
    direction_unit.unit_key = "direction"
    delivery_unit = _unit()
    delivery_unit.id = "delivery"
    delivery_unit.unit_key = "delivery"
    relation = OrganizationRelationDB(
        id="relation-direction-delivery",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        relation_key="direction_governs_delivery",
        kind="governs",
        source_unit_id="direction",
        target_unit_id="delivery",
        handoff_definition_key="lean_direction_goal_handoff",
        handoff_definition_version=1,
        dependency_policy="declared",
        lifecycle="active",
    )

    edges = service._cross_team_artifact_edges(
        teams=teams,
        units=[direction_unit, delivery_unit],
        relations=[relation],
        handoff_definitions={
            "lean_direction_goal_handoff@1": {
                "required_artifact_kinds": ["company_goal", "accepted_requirements"],
                "acceptance_gate_ref": "lean_direction_goal_accepted@1",
            }
        },
    )

    assert len(edges) == 2
    input_edge = next(edge for edge in edges if edge["type"] == "produces_input")
    assert input_edge["reason_code"] == "organization_cross_team_handoff_input_declared"
    assert input_edge["metadata"]["artifacts"] == ["accepted_requirements", "company_goal"]
    handoff_edge = next(edge for edge in edges if edge["type"] == "declares_handoff")
    assert handoff_edge["source"] == {"kind": "team_unit", "ref": "direction"}
    assert handoff_edge["target"] == {"kind": "team_unit", "ref": "delivery"}
    assert handoff_edge["metadata"] == {
        "relation_key": "direction_governs_delivery",
        "handoff_ref": "lean_direction_goal_handoff@1",
        "dependency_policy": "declared",
        "required_artifact_kinds": ["company_goal", "accepted_requirements"],
        "acceptance_gate_ref": "lean_direction_goal_accepted@1",
    }
    assert target_step["activation"]["declared_input_sources"] == [
        {
            "artifacts": ["accepted_requirements", "company_goal"],
            "source_step_ref": source_step["step_ref"],
            "source_owner_role_ref": "portfolio_product_owner@1",
            "source_team_unit_id": "direction",
            "handoff_ref": "lean_direction_goal_handoff@1",
            "relation_key": "direction_governs_delivery",
        }
    ]


def test_declared_handoff_edges_survive_without_artifact_overlap_and_have_distinct_ids() -> None:
    service = OrganizationRoleActivationReadService(catalog=object())
    source_step = {
        "step_ref": "team:enablement/workflow:enablement@1/step:operate",
        "owner_role_ref": "sre@1",
        "outputs": ["operational_readiness"],
        "handoff_ref": "enablement_handoff@1",
        "activation": {"external_inputs": []},
    }
    target_step = {
        "step_ref": "team:delivery/workflow:delivery@1/step:build",
        "owner_role_ref": "developer@1",
        "outputs": ["increment"],
        "handoff_ref": None,
        "activation": {"external_inputs": ["accepted_requirements"]},
    }
    teams = [
        {"team_unit_id": "enablement", "workflow": {"steps": [source_step]}},
        {"team_unit_id": "delivery", "workflow": {"steps": [target_step]}},
    ]
    enablement = _unit()
    enablement.id = "enablement"
    delivery = _unit()
    delivery.id = "delivery"
    relations = [
        OrganizationRelationDB(
            id="relation-1",
            tenant_id="tenant-1",
            project_id="project-1",
            organization_id="organization-1",
            relation_key="enablement_advises_delivery",
            kind="advises",
            source_unit_id="enablement",
            target_unit_id="delivery",
            handoff_definition_key="enablement_handoff",
            handoff_definition_version=1,
            dependency_policy="advisory",
            lifecycle="active",
        ),
        OrganizationRelationDB(
            id="relation-2",
            tenant_id="tenant-1",
            project_id="project-1",
            organization_id="organization-1",
            relation_key="enablement_reviews_delivery",
            kind="reviews",
            source_unit_id="enablement",
            target_unit_id="delivery",
            handoff_definition_key="enablement_handoff",
            handoff_definition_version=1,
            dependency_policy="gate",
            lifecycle="active",
        ),
    ]

    edges = service._cross_team_artifact_edges(
        teams=teams,
        units=[enablement, delivery],
        relations=relations,
        handoff_definitions={
            "enablement_handoff@1": {
                "required_artifact_kinds": ["operational_readiness"],
                "acceptance_gate_ref": "readiness_accepted@1",
            }
        },
    )

    handoff_edges = [edge for edge in edges if edge["type"] == "declares_handoff"]
    assert len(handoff_edges) == 2
    assert len({edge["edge_id"] for edge in handoff_edges}) == 2
    assert {edge["metadata"]["relation_key"] for edge in handoff_edges} == {
        "enablement_advises_delivery",
        "enablement_reviews_delivery",
    }
    assert not [edge for edge in edges if edge["type"] == "produces_input"]


def test_read_model_queries_each_runtime_row_with_full_organization_scope() -> None:
    session = _Session(
        {
            OrganizationUnitDB: [_unit()],
            OrganizationRoleSlotDB: [
                _slot("slot-lead", "team_lead"),
                _slot("slot-developer", "developer"),
            ],
            OrganizationRoleAssignmentDB: [],
            OrganizationRelationDB: [],
            OrganizationTopologySnapshotDB: [_snapshot()],
            TeamBlueprintRevisionDB: [_team_definition()],
            WorkflowDefinitionRevisionDB: [_workflow_definition()],
        }
    )
    service = OrganizationRoleActivationReadService(
        catalog=object(),
        session_factory=lambda: session,
    )

    service.read(
        tenant_id="tenant-1",
        project_id="project-1",
        organization=_organization(),
    )

    statements_by_entity = {
        statement.column_descriptions[0]["entity"]: str(statement) for statement in session.statements
    }
    for entity in (
        OrganizationUnitDB,
        OrganizationRoleSlotDB,
        OrganizationRoleAssignmentDB,
        OrganizationRelationDB,
        OrganizationTopologySnapshotDB,
        TaskDB,
    ):
        sql = statements_by_entity[entity]
        assert f"{entity.__tablename__}.tenant_id" in sql
        assert f"{entity.__tablename__}.project_id" in sql
        assert f"{entity.__tablename__}.organization_id" in sql
