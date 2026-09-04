from __future__ import annotations

from pathlib import Path

from agent.services.collaboration_budget_service import CollaborationBudgetService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, room, service


def _budget(database: Path) -> CollaborationBudgetService:
    return CollaborationBudgetService(
        CollaborationWorkspaceStore(database),
        limits={
            "tenant": 2,
            "workspace": 2,
            "room": 2,
            "principal": 2,
            "actor": 2,
            "task": 2,
            "provider": 2,
            "intent_chain": 2,
            "connection": 2,
        },
        clock=lambda: 100.0,
    )


def _dimensions():
    return {
        "room": "room-a",
        "principal": "principal-a",
        "actor": "agent-a",
        "task": "task-a",
        "provider": "provider-a",
        "intent_chain": "correlation-a",
        "connection": "connection-a",
    }


def test_budget_enforces_every_required_dimension_and_denial_is_terminal(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Budgets",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room("room-a"),
    )
    budget = _budget(database)
    first = budget.admit(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        traffic_class="agent_intent",
        dimensions=_dimensions(),
    )
    second = budget.admit(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        traffic_class="agent_intent",
        dimensions=_dimensions(),
    )
    denied = budget.admit(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        traffic_class="agent_intent",
        dimensions=_dimensions(),
    )
    assert len(first["counters"]) == 9
    assert all(counter["count"] == 2 for counter in second["counters"])
    assert denied["allowed"] is False
    assert denied["reason_code"] == "collaboration_budget_exhausted"
    assert (denied["retry_allowed"], denied["replan_allowed"]) == (False, False)
    audit_events = CollaborationWorkspaceStore(database).timeline(
        "tenant-a",
        "workspace-a",
        actor_binding_id="human-user-a",
        room_id=None,
        after=0,
        limit=10,
    )["items"]
    assert audit_events[-1]["payload"] == {
        "decision": "denied",
        "reason_code": "collaboration_budget_exhausted",
        "traffic_class": "agent_intent",
        "retry_allowed": False,
        "replan_allowed": False,
    }


def test_cancel_and_revocation_can_never_be_blocked_by_abuse_budget(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    service(database).create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Budgets",
        owner=actor(),
        workspace_id="workspace-a",
    )
    budget = _budget(database)
    for traffic in ("cancel", "revocation"):
        result = budget.admit(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            traffic_class=traffic,
            dimensions=_dimensions(),
        )
        assert result["allowed"] is True
        assert result["reason_code"] == "collaboration_critical_signal_exempt"
