from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_live_control_service import CollaborationLiveControlService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationStoreConflict, CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, room, service


def setup(database: Path, now: list[float]):
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Live control",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room("room-a"),
    )
    for suffix in ("b", "c"):
        workspaces.put_membership(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            actor=actor(f"human-user-{suffix}", subject=f"user-{suffix}"),
            role="member",
            status="active",
        )
    live = CollaborationLiveControlService(
        CollaborationWorkspaceStore(database),
        policy=CollaborationWorkspacePolicy(),
        clock=lambda: now[0],
    )
    return workspaces, live


def test_n_participant_cursors_are_actor_room_view_and_ttl_scoped(tmp_path: Path) -> None:
    now = [100.0]
    _workspaces, live = setup(tmp_path / "state.sqlite3", now)
    for index, suffix in enumerate(("a", "b", "c")):
        cursor = live.publish_cursor(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            room_id="room-a",
            principal_actor_id=f"human-user-{suffix}",
            view_id="editor-main",
            x=index / 10,
            y=index / 10,
            epoch=4,
            ttl_seconds=5,
        )
        assert cursor["actor_binding_id"] == f"human-user-{suffix}"
    visible = live.cursors(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        room_id="room-a",
        principal_actor_id="human-user-a",
        view_id="editor-main",
    )
    assert [item["actor_binding_id"] for item in visible["items"]] == [
        "human-user-a",
        "human-user-b",
        "human-user-c",
    ]
    now[0] = 106.0
    assert (
        live.cursors(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            room_id="room-a",
            principal_actor_id="human-user-a",
            view_id="editor-main",
        )["items"]
        == []
    )


def test_control_grant_is_actor_session_revision_bound_and_locally_revocable(tmp_path: Path) -> None:
    now = [100.0]
    workspaces, live = setup(tmp_path / "state.sqlite3", now)
    grant = live.grant_control(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        room_id="room-a",
        principal_actor_id="human-user-b",
        controller_actor_binding_id="human-user-a",
        session_id="session-a",
        view_id="editor-main",
        epoch=4,
        expected_revision=0,
        ttl_seconds=30,
    )
    assert (grant["controlled_actor_binding_id"], grant["controller_actor_binding_id"], grant["revision"]) == (
        "human-user-b",
        "human-user-a",
        1,
    )
    with pytest.raises(CollaborationStoreConflict, match="revision_conflict"):
        live.grant_control(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            room_id="room-a",
            principal_actor_id="human-user-b",
            controller_actor_binding_id="human-user-c",
            session_id="session-a",
            view_id="editor-main",
            epoch=4,
            expected_revision=0,
            ttl_seconds=30,
        )
    assert (
        live.current_grant(tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-a") == grant
    )
    revoked = live.revoke_control(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        expected_revision=1,
    )
    assert revoked["revoked"] is True

    next_grant = live.grant_control(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        room_id="room-a",
        principal_actor_id="human-user-b",
        controller_actor_binding_id="human-user-a",
        session_id="session-b",
        view_id="editor-main",
        epoch=5,
        expected_revision=0,
        ttl_seconds=30,
    )
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=actor("human-user-b", subject="user-b"),
        role="viewer",
        status="active",
        expected_revision=1,
    )
    assert next_grant["controlled_membership_revision"] == 1
    assert (
        live.current_grant(tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-a") is None
    )
