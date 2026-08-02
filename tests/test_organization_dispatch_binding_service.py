from __future__ import annotations

from agent.db_models import TaskDB
from agent.services.organization_dispatch_binding_service import (
    OrganizationDispatchBindingResolver,
)


def test_dispatch_binding_requires_closed_task_state_and_exact_role_assignment() -> None:
    task = TaskDB(
        id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="org-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        status="assigned",
        status_reason_code="planning_dispatch_intent_created",
        assigned_agent_url="http://worker-1:5000",
    )
    routing = {
        "schema": "organization_routing_decision.v1",
        "effective_policy_hash": "a" * 64,
        "decision_hash": "b" * 64,
        "selected_agent_id": "http://worker-1:5000",
        "selected_assignment_id": "assignment-1",
        "selected_team_id": "team-1",
        "selected_role_slot_id": "slot-1",
    }
    dispatch = {
        "schema": "organization_planning_dispatch.v1",
        "dispatch_intent_id": "dispatch-1",
        "lease_id": "lease-1",
        "attempt": 1,
        "track_revision_id": "track-r1",
        "plan_task_id": "PLAN-1",
        "status": "pending_dispatch",
    }

    assert OrganizationDispatchBindingResolver._task_binding_valid(
        task,
        routing=routing,
        dispatch_binding=dispatch,
    )
    assert not OrganizationDispatchBindingResolver._task_binding_valid(
        task,
        routing={**routing, "selected_role_slot_id": "foreign-slot"},
        dispatch_binding=dispatch,
    )
