from __future__ import annotations

import pytest
from sqlmodel import Session, delete, select

from agent.config import settings
from agent.db_models import (
    AgentInfoDB,
    OrganizationAdminGrantDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    ProjectDB,
    TaskDB,
    TeamDB,
)
from agent.models import TaskStepExecuteRequest, TaskStepProposeRequest
from agent.services._task_scoped_forwarding import (
    forward_task_request_if_remote,
)
from agent.services._task_scoped_step_orchestrator import (
    run_execute_step,
    run_propose_step,
)
from agent.services.organization_instance_application_service import (
    OrganizationInstanceApplicationService,
)
from agent.services.organization_lifecycle_service import (
    OrganizationActivitySnapshot,
    OrganizationLifecycleService,
)
from agent.services.organization_task_dispatch_gate_service import (
    OrganizationTaskDispatchGateService,
)
from agent.services.organization_topology_lifecycle_service import (
    SqlOrganizationTopologyLifecycleService,
)


def test_category_research_is_excluded_from_generic_dispatch_paths() -> None:
    def unexpected_session():
        raise AssertionError(
            "secure research gate must reject before DB access"
        )

    decision = OrganizationTaskDispatchGateService(
        session_factory=unexpected_session
    ).evaluate(
        {
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization_id": "org-1",
            "task_kind": "planning_research",
        }
    )

    assert decision.allowed is False
    assert decision.reason_code == (
        "organization_research_secure_delegation_required"
    )


@pytest.mark.parametrize("phase", ["propose", "execute"])
def test_hub_task_scoped_execution_rejects_category_research_before_work(
    monkeypatch,
    phase: str,
) -> None:
    task = {
        "id": "research-1",
        "organization_id": "org-1",
        "task_kind": "planning_research",
        "assigned_agent_url": "http://research-worker:5000",
    }

    class Service:
        @staticmethod
        def _require_task(task_id):
            assert task_id == "research-1"
            return dict(task)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("secure fence must reject before execution")

    monkeypatch.setattr(settings, "role", "hub")
    if phase == "propose":
        outcome = run_propose_step(
            Service(),
            "research-1",
            TaskStepProposeRequest(),
            cli_runner=unexpected,
            forwarder=unexpected,
            tool_definitions_resolver=unexpected,
        )
    else:
        outcome = run_execute_step(
            Service(),
            "research-1",
            TaskStepExecuteRequest(),
            forwarder=unexpected,
            cli_runner=unexpected,
            tool_definitions_resolver=unexpected,
        )

    assert outcome.code == 409
    assert outcome.data["reason_code"] == (
        "organization_research_secure_delegation_required"
    )


def test_generic_forwarder_cannot_bypass_secure_research_delegation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "role", "hub")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("generic Worker forwarding must stay fenced")

    outcome = forward_task_request_if_remote(
        tid="research-1",
        task={
            "id": "research-1",
            "organization_id": "org-1",
            "task_kind": "planning_research",
            "assigned_agent_url": "http://research-worker:5000",
        },
        endpoint="/tasks/research-1/step/propose",
        payload={},
        forwarder=unexpected,
        on_success=unexpected,
    )

    assert outcome is not None
    assert outcome.code == 409
    assert outcome.data["reason_code"] == (
        "organization_research_secure_delegation_required"
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("draft", "validated"),
        ("draft", "archived"),
        ("validated", "active"),
        ("validated", "draft"),
        ("validated", "archived"),
        ("active", "paused"),
        ("active", "completed"),
        ("paused", "active"),
        ("paused", "completed"),
        ("paused", "archived"),
        ("completed", "archived"),
        ("archived", "validated"),
    ],
)
def test_declared_lifecycle_transitions_are_allowed(source: str, target: str) -> None:
    plan = OrganizationLifecycleService().plan_transition(
        organization_id="organization-a",
        current_state=source,
        target_state=target,
        activity=OrganizationActivitySnapshot(),
    )

    assert plan.allowed is True
    assert plan.starts_workers is False
    assert plan.reruns_tasks is False


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("draft", "active"),
        ("active", "archived"),
        ("completed", "active"),
        ("archived", "active"),
    ],
)
def test_undeclared_lifecycle_transitions_are_rejected(source: str, target: str) -> None:
    plan = OrganizationLifecycleService().plan_transition(
        organization_id="organization-a",
        current_state=source,
        target_state=target,
        activity=OrganizationActivitySnapshot(),
    )

    assert plan.allowed is False


def test_completion_with_active_work_requires_explicit_strategy() -> None:
    activity = OrganizationActivitySnapshot(
        running_task_ids=("task-a",),
        active_lease_ids=("lease-a",),
        open_gate_ids=("gate-a",),
        open_handoff_ids=("handoff-a",),
    )
    service = OrganizationLifecycleService()

    blocked = service.plan_transition(
        organization_id="organization-a",
        current_state="active",
        target_state="completed",
        activity=activity,
    )
    drained = service.plan_transition(
        organization_id="organization-a",
        current_state="active",
        target_state="completed",
        activity=activity,
        active_work_strategy="drain",
    )

    assert blocked.allowed is False
    assert blocked.reason_code == "organization_active_work_strategy_required"
    assert drained.allowed is True
    assert "drain_running_tasks" in drained.required_operations
    assert "resolve_open_handoffs" in drained.required_operations


def test_archived_recovery_preserves_lineage_without_automatic_execution() -> None:
    plan = OrganizationLifecycleService().plan_transition(
        organization_id="organization-a",
        current_state="archived",
        target_state="validated",
        activity=OrganizationActivitySnapshot(),
    )

    assert plan.allowed is True
    assert plan.reason_code == "organization_recovery_requires_new_activation"
    assert "create_new_activation_candidate" in plan.required_operations
    assert {"goals", "tasks", "assignments", "artifacts", "audit"}.issubset(plan.preserves_lineage)
    assert plan.starts_workers is False
    assert plan.reruns_tasks is False


def test_activation_plan_promotes_materialized_topology_without_starting_workers(
    db_session,
) -> None:
    tenant_id = "tenant-topology-activation"
    project_id = "project-topology-activation"
    organization_id = "organization-topology-activation"
    source_unit_id = "unit-topology-source"
    team_unit_id = "unit-topology-team"
    team_id = "team-topology-activation"
    db_session.add(
        ProjectDB(
            tenant_id=tenant_id,
            project_id=project_id,
            name="Topology activation",
            created_by_subject_id="owner-a",
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationInstanceDB(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            name="Topology activation",
            definition_key="standard",
            definition_version=1,
            definition_revision="a" * 64,
            effective_limit_profile_ref="standard@1",
            effective_limit_profile_revision=1,
            effective_limit_profile_hash="b" * 64,
            composition_mode="standard",
            plan_digest="c" * 64,
            idempotency_key="instantiate-topology-activation",
            lifecycle="validated",
        )
    )
    db_session.add(TeamDB(id=team_id, name="Topology team", is_active=False))
    db_session.flush()
    db_session.add(
        OrganizationUnitDB(
            id=source_unit_id,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_key="coordination",
            name="Coordination",
            unit_kind="coordination_unit",
            lifecycle="planned",
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationUnitDB(
            id=team_unit_id,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_key="research-team",
            name="Research team",
            unit_kind="team",
            parent_unit_id=source_unit_id,
            lifecycle="planned",
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationTeamLinkDB(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_id=team_unit_id,
            team_id=team_id,
            lifecycle="planned",
        )
    )
    db_session.add(
        OrganizationRoleSlotDB(
            id="slot-topology-activation",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_id=team_unit_id,
            slot_key="researcher",
            role_template_key="researcher",
            role_template_version=1,
            lifecycle="planned",
        )
    )
    db_session.add(
        OrganizationRelationDB(
            id="relation-topology-activation",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            relation_key="coordination-to-research",
            kind="coordinates",
            source_unit_id=source_unit_id,
            target_unit_id=team_unit_id,
            lifecycle="planned",
        )
    )
    db_session.flush()

    plan = OrganizationLifecycleService().plan_transition(
        organization_id=organization_id,
        current_state="validated",
        target_state="active",
        activity=OrganizationActivitySnapshot(),
    )
    result = SqlOrganizationTopologyLifecycleService().activate_planned(
        session=db_session,
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        now=1234.0,
    )
    db_session.flush()

    units = db_session.exec(
        select(OrganizationUnitDB).where(
            OrganizationUnitDB.organization_id == organization_id
        )
    ).all()
    link = db_session.exec(
        select(OrganizationTeamLinkDB).where(
            OrganizationTeamLinkDB.organization_id == organization_id
        )
    ).one()
    slot = db_session.get(OrganizationRoleSlotDB, "slot-topology-activation")
    relation = db_session.get(
        OrganizationRelationDB,
        "relation-topology-activation",
    )
    team = db_session.get(TeamDB, team_id)
    observed = {
        "unit_lifecycles": {row.lifecycle for row in units},
        "link_lifecycle": link.lifecycle,
        "link_activated_at": link.activated_at,
        "slot_lifecycle": slot.lifecycle if slot is not None else None,
        "relation_lifecycle": (
            relation.lifecycle if relation is not None else None
        ),
        "team_active": team.is_active if team is not None else None,
    }
    db_session.rollback()

    assert "activate_planned_topology" in plan.required_operations
    assert plan.starts_workers is False
    assert result.as_dict() == {
        "activated_units": 2,
        "activated_team_links": 1,
        "activated_teams": 1,
        "activated_role_slots": 1,
        "activated_relations": 1,
    }
    assert observed == {
        "unit_lifecycles": {"active"},
        "link_lifecycle": "active",
        "link_activated_at": 1234.0,
        "slot_lifecycle": "active",
        "relation_lifecycle": "active",
        "team_active": True,
    }


def test_application_lifecycle_fences_queued_work_and_projects_topology(
    db_session,
) -> None:
    from agent.database import engine

    tenant_id = "tenant-lifecycle-application"
    project_id = "project-lifecycle-application"
    organization_id = "organization-lifecycle-application"
    unit_id = "unit-lifecycle-application"
    team_id = "team-lifecycle-application"
    principal_id = "owner-lifecycle-application"
    grant_id = "grant-lifecycle-application"
    task_id = "task-lifecycle-queued"
    active_agent_url = "http://lifecycle-active-worker:5000"
    manual_agent_url = "http://lifecycle-manual-worker:5000"
    plan_digest = "d" * 64
    db_session.add(
        ProjectDB(
            tenant_id=tenant_id,
            project_id=project_id,
            name="Lifecycle application",
            created_by_subject_id=principal_id,
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationInstanceDB(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            name="Lifecycle application",
            definition_key="standard",
            definition_version=1,
            definition_revision="a" * 64,
            effective_limit_profile_ref="standard@1",
            effective_limit_profile_revision=1,
            effective_limit_profile_hash="b" * 64,
            composition_mode="standard",
            plan_digest=plan_digest,
            idempotency_key="instantiate-lifecycle-application",
            lifecycle="validated",
        )
    )
    db_session.add(TeamDB(id=team_id, name="Lifecycle team"))
    db_session.add(
        AgentInfoDB(
            url=active_agent_url,
            name="Lifecycle active worker",
        )
    )
    db_session.add(
        AgentInfoDB(
            url=manual_agent_url,
            name="Lifecycle manually suspended worker",
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationUnitDB(
            id=unit_id,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_key="research-team",
            name="Research team",
            unit_kind="team",
            lifecycle="planned",
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationTeamLinkDB(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_id=unit_id,
            team_id=team_id,
            lifecycle="planned",
        )
    )
    db_session.add(
        OrganizationRoleSlotDB(
            id="slot-lifecycle-application",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_id=unit_id,
            slot_key="researcher",
            role_template_key="researcher",
            role_template_version=1,
            lifecycle="planned",
        )
    )
    db_session.add(
        OrganizationMembershipDB(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            membership_kind="organization_admin",
        )
    )
    db_session.add(
        OrganizationAdminGrantDB(
            grant_id=grant_id,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            grant_kind="organization_lifecycle",
            policy_hash=plan_digest,
            granted_by=principal_id,
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationRoleAssignmentDB(
            id="assignment-lifecycle-active",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            role_slot_id="slot-lifecycle-application",
            agent_url=active_agent_url,
            lifecycle="active",
            assignment_metadata={"label": "active-assignment"},
        )
    )
    db_session.add(
        OrganizationRoleAssignmentDB(
            id="assignment-lifecycle-manual",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            role_slot_id="slot-lifecycle-application",
            agent_url=manual_agent_url,
            lifecycle="suspended",
            assignment_metadata={
                "label": "manual-assignment",
                "suspended_by": "manual",
            },
        )
    )
    db_session.add(
        TaskDB(
            id=task_id,
            title="Queued research",
            status="todo",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_id=unit_id,
            team_id=team_id,
            role_slot_id="slot-lifecycle-application",
        )
    )
    db_session.commit()

    service = OrganizationInstanceApplicationService(catalog=None)  # type: ignore[arg-type]

    activated = service.transition_lifecycle(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        principal_id=principal_id,
        grant_id=grant_id,
        expected_lock_version=1,
        idempotency_key="lifecycle-activate",
        target_state="active",
        active_work_strategy=None,
        activity=OrganizationActivitySnapshot(),
    )
    paused = service.transition_lifecycle(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        principal_id=principal_id,
        grant_id=grant_id,
        expected_lock_version=2,
        idempotency_key="lifecycle-pause",
        target_state="paused",
        active_work_strategy=None,
        activity=OrganizationActivitySnapshot(),
    )

    with Session(engine) as session:
        queued = session.get(TaskDB, task_id)
        link = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.organization_id == organization_id
            )
        ).one()
        team = session.get(TeamDB, team_id)
        active_assignment = session.get(
            OrganizationRoleAssignmentDB,
            "assignment-lifecycle-active",
        )
        manual_assignment = session.get(
            OrganizationRoleAssignmentDB,
            "assignment-lifecycle-manual",
        )
        paused_decision = OrganizationTaskDispatchGateService(
            session_factory=lambda: Session(engine)
        ).evaluate(queued)
        assert queued is not None and queued.status == "todo"
        assert link.lifecycle == "draining"
        assert team is not None and team.is_active is False
        assert active_assignment is not None
        assert active_assignment.lifecycle == "suspended"
        assert active_assignment.assignment_metadata == {
            "label": "active-assignment",
            "suspended_by": "organization_pause",
        }
        assert manual_assignment is not None
        assert manual_assignment.lifecycle == "suspended"
        assert manual_assignment.assignment_metadata == {
            "label": "manual-assignment",
            "suspended_by": "manual",
        }
        assert paused_decision.allowed is False

    resumed = service.transition_lifecycle(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        principal_id=principal_id,
        grant_id=grant_id,
        expected_lock_version=3,
        idempotency_key="lifecycle-resume",
        target_state="active",
        active_work_strategy=None,
        activity=OrganizationActivitySnapshot(),
    )
    with Session(engine) as session:
        active_assignment = session.get(
            OrganizationRoleAssignmentDB,
            "assignment-lifecycle-active",
        )
        manual_assignment = session.get(
            OrganizationRoleAssignmentDB,
            "assignment-lifecycle-manual",
        )
        assert active_assignment is not None
        assert active_assignment.lifecycle == "active"
        assert active_assignment.assignment_metadata == {
            "label": "active-assignment"
        }
        assert manual_assignment is not None
        assert manual_assignment.lifecycle == "suspended"
        assert manual_assignment.assignment_metadata == {
            "label": "manual-assignment",
            "suspended_by": "manual",
        }
    completed = service.transition_lifecycle(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        principal_id=principal_id,
        grant_id=grant_id,
        expected_lock_version=4,
        idempotency_key="lifecycle-complete",
        target_state="completed",
        active_work_strategy="drain",
        activity=OrganizationActivitySnapshot(),
    )
    archived = service.transition_lifecycle(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        principal_id=principal_id,
        grant_id=grant_id,
        expected_lock_version=5,
        idempotency_key="lifecycle-archive",
        target_state="archived",
        active_work_strategy="cancel",
        activity=OrganizationActivitySnapshot(),
    )
    recovered = service.transition_lifecycle(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        principal_id=principal_id,
        grant_id=grant_id,
        expected_lock_version=6,
        idempotency_key="lifecycle-recover",
        target_state="validated",
        active_work_strategy=None,
        activity=OrganizationActivitySnapshot(),
    )

    with Session(engine) as session:
        task = session.get(TaskDB, task_id)
        link = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.organization_id == organization_id
            )
        ).one()
        active_assignment = session.get(
            OrganizationRoleAssignmentDB,
            "assignment-lifecycle-active",
        )
        manual_assignment = session.get(
            OrganizationRoleAssignmentDB,
            "assignment-lifecycle-manual",
        )
        assert task is not None and task.status == "cancelled"
        assert link.lifecycle == "planned"
        assert active_assignment is not None
        assert active_assignment.lifecycle == "ended"
        assert active_assignment.assignment_metadata == {
            "label": "active-assignment"
        }
        assert manual_assignment is not None
        assert manual_assignment.lifecycle == "ended"
        assert manual_assignment.assignment_metadata == {
            "label": "manual-assignment"
        }
        assert (
            OrganizationTaskDispatchGateService(
                session_factory=lambda: Session(engine)
            ).evaluate(task).allowed
            is False
        )

    assert activated["topology_activation"]["action"] == "activate"
    assert paused["active_work"] is None
    assert paused["topology_projection"]["action"] == "pause"
    assert resumed["topology_projection"]["action"] == "activate"
    assert task_id in completed["active_work"]["source_task_ids"]
    assert archived["topology_projection"]["action"] == "archive"
    assert recovered["topology_projection"]["action"] == (
        "prepare_activation_candidate"
    )

    # This test commits deliberately so the application service can open its
    # own production-shaped UoW. Remove the scoped aggregate in FK-safe order.
    with Session(engine) as session:
        session.exec(delete(TaskDB).where(TaskDB.id == task_id))
        session.exec(
            delete(OrganizationInstanceDB).where(
                OrganizationInstanceDB.organization_id == organization_id
            )
        )
        session.exec(
            delete(AgentInfoDB).where(
                AgentInfoDB.url.in_((active_agent_url, manual_agent_url))
            )
        )
        session.exec(delete(TeamDB).where(TeamDB.id == team_id))
        session.exec(
            delete(ProjectDB).where(
                ProjectDB.tenant_id == tenant_id,
                ProjectDB.project_id == project_id,
            )
        )
        session.commit()
