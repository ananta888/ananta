from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_delivery_service import CollaborationProjectionService
from agent.services.collaboration_recovery_service import CollaborationRecoveryService, content_safe_diagnostics
from agent.services.collaboration_search_service import CollaborationSearchService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, build_event, room, service


def _recovery(database: Path) -> CollaborationRecoveryService:
    store = CollaborationWorkspaceStore(database)
    policy = CollaborationWorkspacePolicy()
    return CollaborationRecoveryService(
        store,
        policy=policy,
        projections=CollaborationProjectionService(store),
        search=CollaborationSearchService(store, policy=policy),
    )


def _append(workspaces, room_id: str, text: str, key: str) -> None:
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=build_event(
            workspace_id="workspace-a",
            room_id=room_id,
            actor_binding_id="human-user-a",
            event_type="message.posted",
            payload={"text": text},
            idempotency_key=key,
        ),
    )


def test_backup_restore_rebuilds_projections_and_keeps_rollback(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Recovery",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    _append(workspaces, "room-main", "in backup", "event-one")
    recovery = _recovery(database)
    backup = recovery.backup(tmp_path / "backups" / "collaboration.sqlite3")
    _append(workspaces, "room-main", "after backup", "event-two")

    restored = recovery.restore(
        tmp_path / "backups" / "collaboration.sqlite3",
        expected_digest=backup["digest"],
        tenant_id="tenant-a",
        workspace_ids=["workspace-a"],
    )
    assert restored["restored"] is True
    assert restored["external_bridge_required"] is False
    assert restored["rollback_filename"] == "state.sqlite3.rollback"
    assert restored["rebuilt"]["workspace-a"]["search_digest"]
    events = CollaborationWorkspaceStore(database).projection_events("tenant-a", "workspace-a")
    assert [event["payload"]["text"] for event in events] == ["in backup"]


def test_restore_rejects_wrong_digest_without_replacing_live_database(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Recovery",
        owner=actor(),
        workspace_id="workspace-a",
    )
    recovery = _recovery(database)
    backup = recovery.backup(tmp_path / "backup.sqlite3")
    with pytest.raises(ValueError, match="restore_digest_mismatch"):
        recovery.restore(
            tmp_path / "backup.sqlite3",
            expected_digest="0" * 64,
            tenant_id="tenant-a",
            workspace_ids=["workspace-a"],
        )
    assert backup["integrity"] == "ok"
    assert service(database).list_workspaces(tenant_id="tenant-a", principal_actor_id="human-user-a")["items"]


def test_export_filters_private_rooms_and_content_safe_diagnostics_reject_text(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Export",
        owner=actor(),
        workspace_id="workspace-a",
    )
    for room_id in ("room-public", "room-private"):
        workspaces.create_room(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            room=room(room_id),
        )
    editor = actor("human-user-b", subject="user-b")
    workspaces.put_membership(
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
        actor_binding_ids=[],
        expected_revision=1,
    )
    _append(workspaces, "room-public", "public", "public")
    _append(workspaces, "room-private", "private", "private")
    exported = _recovery(database).export_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-b",
    )
    assert [item["room_id"] for item in exported["workspace"]["rooms"]] == ["room-public"]
    assert [item["payload"]["text"] for item in exported["events"]] == ["public"]
    assert exported["contains_key_material"] is False
    assert content_safe_diagnostics({"latency_ms": 12, "queue_depth": 1})["contains_content"] is False
    with pytest.raises(ValueError, match="field_forbidden"):
        content_safe_diagnostics({"message_text": "secret"})
