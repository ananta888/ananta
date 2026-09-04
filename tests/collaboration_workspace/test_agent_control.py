from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.services.collaboration_agent_control_service import CollaborationAgentControlService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import (
    CollaborationStoreConflict,
    CollaborationWorkspaceStore,
)
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import actor, room, service


class AssignmentAuthority:
    def __init__(self, *, authorized: bool = True) -> None:
        self.calls = 0
        self.authorized = authorized

    def decide(self, *, tenant_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        assert tenant_id == "tenant-a"
        assert "worker_id" not in intent["payload"]
        return {
            "authorized": self.authorized,
            "reason_code": "hub_assignment_reserved" if self.authorized else "hub_policy_denied",
            "assignment": {
                "task_id": intent["task_id"],
                "assignment_id": "assignment-a",
                "worker_id": "worker-hub-selected",
                "allowed_operations": ["repo.read"],
                "budget_units": 100,
                "duration_seconds": 60,
            }
            if self.authorized
            else {},
        }


def _setup(database: Path, authority: AssignmentAuthority, *, loop_limit: int = 16):
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Agents",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=actor("agent-a", kind="agent", authority="hub_agent", subject="registered-agent-a"),
        role="member",
        status="active",
    )
    return CollaborationAgentControlService(
        CollaborationWorkspaceStore(database),
        policy=CollaborationWorkspacePolicy(),
        assignment_authority=authority,
        clock=lambda: 100.0,
        maximum_correlation_intents=loop_limit,
    )


def _intent(*, intent_id: str = "intent-a", correlation_id: str = "correlation-a") -> dict[str, Any]:
    payload = {"request": "analyze repository"}
    return {
        "schema": "ananta.collaboration-agent-intent.v1",
        "intent_id": intent_id,
        "workspace_id": "workspace-a",
        "room_id": "room-main",
        "actor_binding_id": "human-user-a",
        "intent_type": "propose_task",
        "target_actor_binding_id": "agent-a",
        "task_id": "task-a",
        "correlation_id": correlation_id,
        "causation_id": None,
        "hop_count": 0,
        "payload": payload,
        "payload_digest": canonical_digest(payload),
    }


def _offer(*, attestation: str = "verified", offer_id: str = "offer-a") -> dict[str, Any]:
    return {
        "schema": "ananta.collaboration-resource-offer.v1",
        "offer_id": offer_id,
        "workspace_id": "workspace-a",
        "owner_actor_binding_id": "human-user-a",
        "resource_id": "resource-a",
        "capability_category": "repository",
        "capacity_class": "medium",
        "scopes": ["repo.read"],
        "expires_at": 1000.0,
        "sensitivity": "workspace",
        "attestation_status": attestation,
        "metadata": {"label": "Repository mirror"},
    }


def test_room_intent_is_decided_once_by_hub_and_cannot_choose_worker(tmp_path: Path) -> None:
    authority = AssignmentAuthority()
    control = _setup(tmp_path / "state.sqlite3", authority)
    intent = _intent()
    admitted = control.propose_intent(tenant_id="tenant-a", principal_actor_id="human-user-a", intent=intent)
    replay = control.propose_intent(tenant_id="tenant-a", principal_actor_id="human-user-a", intent=intent)
    assert admitted["state"] == "accepted"
    assert admitted["assignment"]["worker_id"] == "worker-hub-selected"
    assert (authority.calls, replay["replayed"], replay["worker_invoked"]) == (1, True, False)
    escalated_payload = {"request": "analyze", "worker_id": "caller-selected"}
    escalated = {
        **_intent(intent_id="intent-escalated"),
        "payload": escalated_payload,
        "payload_digest": canonical_digest(escalated_payload),
    }
    with pytest.raises(ValueError, match="authority_escalation"):
        control.propose_intent(tenant_id="tenant-a", principal_actor_id="human-user-a", intent=escalated)
    nested_payload = {"request": {"tools": ["caller-selected"]}}
    nested = {
        **_intent(intent_id="intent-nested"),
        "payload": nested_payload,
        "payload_digest": canonical_digest(nested_payload),
    }
    with pytest.raises(ValueError, match="authority_escalation"):
        control.propose_intent(tenant_id="tenant-a", principal_actor_id="human-user-a", intent=nested)


def test_agent_intent_loop_limit_is_bounded(tmp_path: Path) -> None:
    authority = AssignmentAuthority()
    control = _setup(tmp_path / "state.sqlite3", authority, loop_limit=1)
    control.propose_intent(tenant_id="tenant-a", principal_actor_id="human-user-a", intent=_intent())
    with pytest.raises(CollaborationStoreConflict, match="loop_limit"):
        control.propose_intent(
            tenant_id="tenant-a",
            principal_actor_id="human-user-a",
            intent=_intent(intent_id="intent-b"),
        )


def test_resource_lease_is_hub_bound_fenced_and_cancelable(tmp_path: Path) -> None:
    authority = AssignmentAuthority()
    control = _setup(tmp_path / "state.sqlite3", authority)
    assert (
        control.publish_offer(tenant_id="tenant-a", principal_actor_id="human-user-a", offer=_offer())["lease_granted"]
        is False
    )
    assignment = authority.decide(tenant_id="tenant-a", intent=_intent())["assignment"]
    lease = control.reserve_resource_lease(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        offer_id="offer-a",
        assignment=assignment,
    )
    admitted = control.admit_resource_result(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        lease_id=lease["lease_id"],
        task_id="task-a",
        assignment_id="assignment-a",
        fencing_token=lease["fencing_token"],
    )
    assert admitted["accepted"] is True
    with pytest.raises(CollaborationStoreConflict, match="result_binding_rejected"):
        control.admit_resource_result(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            lease_id=lease["lease_id"],
            task_id="task-a",
            assignment_id="assignment-a",
            fencing_token=lease["fencing_token"] + 1,
        )
    assert (
        control.cancel_task(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            task_id="task-a",
        )["revoked_leases"]
        == 1
    )
    replay = control.reserve_resource_lease(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        offer_id="offer-a",
        assignment=assignment,
    )
    assert (replay["replayed"], replay["status"]) == (True, "revoked")
    with pytest.raises(CollaborationStoreConflict, match="result_binding_rejected"):
        control.admit_resource_result(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            lease_id=lease["lease_id"],
            task_id="task-a",
            assignment_id="assignment-a",
            fencing_token=lease["fencing_token"],
        )


def test_unverified_offer_cannot_grant_production_lease(tmp_path: Path) -> None:
    authority = AssignmentAuthority()
    control = _setup(tmp_path / "state.sqlite3", authority)
    control.publish_offer(
        tenant_id="tenant-a",
        principal_actor_id="human-user-a",
        offer=_offer(attestation="unverified"),
    )
    with pytest.raises(PermissionError, match="attestation_required"):
        control.reserve_resource_lease(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            offer_id="offer-a",
            assignment=authority.decide(tenant_id="tenant-a", intent=_intent())["assignment"],
        )


def test_agent_profile_is_metadata_and_registration_remains_authoritative(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Profiles",
        owner=actor(),
        workspace_id="workspace-a",
    )
    agent = {
        **actor("agent-a", kind="agent", authority="hub_agent", subject="registered-agent-a"),
        "profile": {"provider": "local", "model": "coder", "profile_revision": "profile-v2"},
    }
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=agent,
        role="member",
        status="active",
    )
    detail = workspaces.get_workspace(
        tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-a"
    )
    stored = next(item for item in detail["memberships"] if item["actor_binding_id"] == "agent-a")
    assert stored["actor"]["profile"] == agent["profile"]
    assert stored["actor"]["authority_subject"] == "registered-agent-a"
    assert "worker_id" not in stored["actor"]["profile"]
