from __future__ import annotations

import copy
import json
import sqlite3
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


def test_membership_is_revisioned_idempotent_and_rejects_identity_rebinding(tmp_path: Path) -> None:
    database = tmp_path / "collaboration.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Membership",
        owner=actor(),
        workspace_id="workspace-a",
    )
    member = actor("agent-a", kind="agent", authority="hub_agent", subject="registered-agent-a")
    added = workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=member,
        role="member",
        status="active",
    )
    replay = workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=member,
        role="member",
        status="active",
        expected_revision=added["revision"],
    )
    assert (replay["revision"], replay["replayed"]) == (1, True)
    rebound = {**member, "authority_subject": "different-agent"}
    with pytest.raises(CollaborationStoreConflict, match="actor_binding_conflict"):
        workspaces.put_membership(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            actor=rebound,
            role="member",
            status="active",
            expected_revision=1,
        )
    revoked = workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=member,
        role="member",
        status="revoked",
        expected_revision=1,
    )
    assert (revoked["revision"], revoked["effective_capabilities"]) == (2, [])
    with sqlite3.connect(database) as connection:
        history = connection.execute(
            "SELECT revision FROM collaboration_membership_history WHERE tenant_id='tenant-a' "
            "AND workspace_id='workspace-a' AND actor_binding_id='agent-a' ORDER BY revision"
        ).fetchall()
    assert history == [(1,), (2,)]


def test_revocation_rotates_security_epoch_and_private_presence_does_not_leak(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "state.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Presence",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room("room-private"),
    )
    editor = actor("human-user-b", subject="user-b")
    member = workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=editor,
        role="member",
        status="active",
    )
    workspaces.put_room_access(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room_id="room-private",
        access_mode="restricted",
        actor_binding_ids=["human-user-b"],
        expected_revision=1,
    )
    workspaces.renew_presence(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-b",
        lease_id="presence-b",
        ttl_seconds=30,
        epoch=1,
    )
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=editor,
        role="member",
        status="revoked",
        expected_revision=member["revision"],
    )
    with pytest.raises(CollaborationStoreConflict, match="security_epoch_stale"):
        workspaces.renew_presence(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            lease_id="presence-owner",
            ttl_seconds=30,
            epoch=1,
        )
    workspaces.renew_presence(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        lease_id="presence-owner",
        ttl_seconds=30,
        epoch=2,
    )
    presence = workspaces.room_presence(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        room_id="room-private",
        principal_actor_id="human-user-a",
    )
    assert [item["actor_binding_id"] for item in presence["items"]] == ["human-user-a"]
    assert presence["items"][0]["membership_authority"] is False


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


def test_restricted_rooms_do_not_leak_through_lists_timeline_search_or_threads(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Private Project",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room("room-private"),
    )
    editor = actor("human-user-b", subject="user-b")
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=editor,
        role="editor",
        status="active",
    )
    workspaces.put_room_access(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room_id="room-private",
        access_mode="restricted",
        actor_binding_ids=[],
        expected_revision=1,
    )
    root = build_event(
        workspace_id="workspace-a",
        room_id="room-private",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "private needle"},
        idempotency_key="private-root",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=root,
    )

    assert (
        workspaces.get_workspace(tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-b")[
            "rooms"
        ]
        == []
    )
    assert (
        workspaces.timeline(tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-b")[
            "items"
        ]
        == []
    )
    assert (
        workspaces.search(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-b",
            query="needle",
        )["items"]
        == []
    )
    with pytest.raises(KeyError, match="thread_not_found"):
        workspaces.thread(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-b",
            thread_id=root["event_id"],
        )
    with pytest.raises(PermissionError, match="room_visibility_denied"):
        workspaces.acknowledge(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-b",
            room_id="room-private",
            sequence=1,
        )


def test_thread_projection_is_idempotent_revisioned_and_append_only(tmp_path: Path) -> None:
    database = tmp_path / "collaboration.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Threads",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    root = build_event(
        workspace_id="workspace-a",
        room_id="room-main",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "root"},
        idempotency_key="thread-root",
    )
    workspaces.append_event(
        tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-a", event=root
    )

    def mutate(event_type: str, key: str, expected_revision: int) -> dict[str, object]:
        event = build_event(
            workspace_id="workspace-a",
            room_id="room-main",
            thread_id=root["event_id"],
            actor_binding_id="human-user-a",
            event_type=event_type,
            payload={"expected_thread_revision": expected_revision, "text": key},
            idempotency_key=key,
        )
        return workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=event,
        )

    reply = mutate("message.replied", "reply", 1)
    replay = workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event={
            key: value
            for key, value in reply.items()
            if key
            not in {
                "tenant_id",
                "sequence",
                "admitted_at",
                "replayed",
                "human_intervention_required",
            }
        },
    )
    assert replay["replayed"] is True
    with pytest.raises(CollaborationStoreConflict, match="thread_revision_conflict"):
        mutate("message.replied", "stale-reply", 1)
    mutate("thread.resolved", "resolve", 2)
    with pytest.raises(CollaborationStoreConflict, match="thread_state_conflict"):
        mutate("thread.resolved", "resolve-again", 3)
    mutate("thread.reopened", "reopen", 3)
    mutate("thread.tombstoned", "tombstone", 4)
    projection = workspaces.thread(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        thread_id=root["event_id"],
    )
    assert (projection["status"], projection["revision"], len(projection["events"])) == ("tombstoned", 5, 5)
    with pytest.raises(CollaborationStoreConflict, match="thread_tombstoned"):
        mutate("thread.reopened", "late-reopen", 5)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM collaboration_events").fetchone()[0] == 5


def test_legacy_sqlite_schema_is_upgraded_and_room_access_is_backfilled(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE collaboration_rooms(
                tenant_id TEXT,workspace_id TEXT,room_id TEXT,payload_json TEXT,
                PRIMARY KEY(tenant_id,workspace_id,room_id)
            );
            CREATE TABLE collaboration_events(
                tenant_id TEXT,workspace_id TEXT,sequence INTEGER,event_id TEXT,
                idempotency_key TEXT,payload_json TEXT,
                PRIMARY KEY(tenant_id,workspace_id,sequence),
                UNIQUE(tenant_id,workspace_id,event_id),
                UNIQUE(tenant_id,workspace_id,idempotency_key)
            );
            INSERT INTO collaboration_rooms VALUES('tenant-a','workspace-a','room-a','{"room_id":"room-a"}');
            """
        )
    from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore

    CollaborationWorkspaceStore(database)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(collaboration_events)")}
        access = connection.execute(
            "SELECT access_mode,revision FROM collaboration_room_access "
            "WHERE tenant_id='tenant-a' AND workspace_id='workspace-a' AND room_id='room-a'"
        ).fetchone()
    assert {"room_id", "thread_id", "event_type", "visibility", "admitted_at"} <= columns
    assert access == ("workspace", 1)


def test_room_archive_snapshots_history_and_requires_authorized_reopen(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "state.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Lifecycle",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    first = build_event(
        workspace_id="workspace-a",
        room_id="room-main",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "before archive"},
        idempotency_key="before-archive",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=first,
    )
    archived = workspaces.transition_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room_id="room-main",
        target_state="archived",
        expected_revision=1,
    )
    assert archived["checkpoint"] == 1
    assert len(archived["snapshot_digest"]) == 64
    with pytest.raises(CollaborationStoreConflict, match="room_not_active"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=build_event(
                workspace_id="workspace-a",
                room_id="room-main",
                actor_binding_id="human-user-a",
                event_type="message.posted",
                payload={"text": "blocked"},
                idempotency_key="blocked-archive",
            ),
        )
    reopened = workspaces.transition_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room_id="room-main",
        target_state="active",
        expected_revision=2,
    )
    assert reopened["revision"] == 3
