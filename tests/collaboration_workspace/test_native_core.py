from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.services.collaboration_workspace_policy import CollaborationPolicyDenied
from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import CollaborationContractError, WorkspaceEventV1
from tests.collaboration_workspace.helpers import actor, build_event, room, service


def test_contract_schemas_are_closed() -> None:
    for path in Path("schemas/collaboration").glob("*.json"):
        schema = json.loads(path.read_text())
        assert schema["additionalProperties"] is False


def test_event_contract_rejects_tampering_and_invented_evidence() -> None:
    event = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "hello"},
        idempotency_key="message-one",
    )
    event["payload"]["text"] = "tampered"
    with pytest.raises(CollaborationContractError, match="payload_digest_mismatch"):
        WorkspaceEventV1.from_mapping(event)

    event = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "hello"},
        idempotency_key="message-two",
    )
    event["source_refs"] = ["made-up-source"]
    with pytest.raises(CollaborationContractError, match="evidence_ref_invalid"):
        WorkspaceEventV1.from_mapping(event)

    event = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="decision.recorded",
        payload={"decision": "release"},
        idempotency_key="decision-one",
    )
    with pytest.raises(CollaborationContractError, match="grounded_evidence_required"):
        WorkspaceEventV1.from_mapping(event)


def test_native_workspace_event_replay_search_cursor_and_presence_are_automatic(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspace = workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Project A",
        owner=actor(),
        workspace_id="workspace-a",
    )
    assert workspace["human_intervention_required"] is False
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    event = build_event(
        workspace_id="workspace-a",
        room_id="room-main",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "searchable automatic message"},
        idempotency_key="message-one",
    )
    appended = workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=event,
    )
    replayed = workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=event,
    )
    assert appended["sequence"] == 1
    assert replayed["replayed"] is True
    assert (
        workspaces.search(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            query="automatic",
        )["items"][0]["event_id"]
        == event["event_id"]
    )
    assert (
        workspaces.acknowledge(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            room_id="room-main",
            sequence=1,
        )["sequence"]
        == 1
    )
    with pytest.raises(CollaborationStoreConflict, match="cursor_regression"):
        workspaces.acknowledge(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            room_id="room-main",
            sequence=0,
        )
    presence = workspaces.renew_presence(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        lease_id="lease-one",
        ttl_seconds=30,
        epoch=1,
    )
    assert presence["epoch"] == 1


def test_membership_revocation_is_immediate_and_tenant_scoped(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Project A",
        owner=actor(),
        workspace_id="workspace-a",
    )
    editor = actor("human-user-b", subject="user-b")
    membership = workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=editor,
        role="editor",
        status="active",
    )
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=editor,
        role="editor",
        status="revoked",
        expected_revision=membership["revision"],
    )
    with pytest.raises(CollaborationPolicyDenied, match="membership_required"):
        workspaces.timeline(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-b",
        )
    with pytest.raises(CollaborationPolicyDenied, match="membership_required"):
        workspaces.get_workspace(
            tenant_id="tenant-b",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
        )


def test_idempotency_conflict_fails_closed(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Project A",
        owner=actor(),
        workspace_id="workspace-a",
    )
    event = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "one"},
        idempotency_key="same-key",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=event,
    )
    conflict = copy.deepcopy(event)
    conflict["event_id"] = "event-other"
    with pytest.raises(CollaborationStoreConflict, match="idempotency_conflict"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=conflict,
        )
