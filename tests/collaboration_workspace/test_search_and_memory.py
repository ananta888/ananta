from __future__ import annotations

from pathlib import Path

from agent.services.collaboration_search_service import CollaborationSearchService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, build_event, room, service


def _setup(database: Path):
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Search",
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
    return workspaces, CollaborationSearchService(
        CollaborationWorkspaceStore(database), policy=CollaborationWorkspacePolicy(), clock=lambda: 100.0
    )


def _append(workspaces, *, room_id: str, text: str, key: str):
    event = build_event(
        workspace_id="workspace-a",
        room_id=room_id,
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": text},
        idempotency_key=key,
    )
    return workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=event,
    )


def test_search_revalidates_room_rights_without_leaking_private_metadata(tmp_path: Path) -> None:
    workspaces, search = _setup(tmp_path / "state.sqlite3")
    _append(workspaces, room_id="room-public", text="shared needle", key="public")
    _append(workspaces, room_id="room-private", text="private needle", key="private")
    search.rebuild("tenant-a", "workspace-a")
    editor = search.query(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-b",
        query="needle",
    )
    assert [item["room_id"] for item in editor["items"]] == ["room-public"]
    assert all("search_text" not in item for item in editor["items"])
    owner = search.query(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        query="needle",
    )
    assert {item["room_id"] for item in owner["items"]} == {"room-public", "room-private"}


def test_tombstone_removes_thread_from_index_and_full_matches_incremental(tmp_path: Path) -> None:
    workspaces, search = _setup(tmp_path / "state.sqlite3")
    root = _append(workspaces, room_id="room-public", text="erasable needle", key="root")
    reply_event = build_event(
        workspace_id="workspace-a",
        room_id="room-public",
        thread_id=root["event_id"],
        actor_binding_id="human-user-a",
        event_type="message.replied",
        payload={"text": "reply needle", "expected_thread_revision": 1},
        idempotency_key="reply",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=reply_event,
    )
    full = search.rebuild("tenant-a", "workspace-a", mode="full")
    incremental = search.rebuild("tenant-a", "workspace-a", mode="incremental")
    assert full["index_digest"] == incremental["index_digest"]
    tombstone = build_event(
        workspace_id="workspace-a",
        room_id="room-public",
        thread_id=root["event_id"],
        actor_binding_id="human-user-a",
        event_type="thread.tombstoned",
        payload={"expected_thread_revision": 2},
        idempotency_key="tombstone",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=tombstone,
    )
    assert search.drift("tenant-a", "workspace-a")["ok"] is False
    search.rebuild("tenant-a", "workspace-a")
    result = search.query(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        query="needle",
    )
    assert result["items"] == []
    assert search.drift("tenant-a", "workspace-a")["ok"] is True


def test_room_memory_and_context_bundle_are_bounded_and_actor_bound(tmp_path: Path) -> None:
    workspaces, search = _setup(tmp_path / "state.sqlite3")
    _append(workspaces, room_id="room-public", text="bounded summary", key="memory")
    memory = search.room_memory(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        room_id="room-public",
        principal_actor_id="human-user-b",
        maximum_events=5,
    )
    assert memory["entries"][0]["summary"] == "bounded summary"
    bundle = search.context_bundle(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        room_id="room-public",
        principal_actor_id="human-user-b",
        task_id="task-a",
        policy={"maximum_events": 5},
        ttl_seconds=30,
    )
    assert (bundle["actor_binding_id"], bundle["expires_at"], bundle["allowed_references"]) == (
        "human-user-b",
        130.0,
        [],
    )


def test_temporal_query_applies_hard_filters_and_room_scope(tmp_path: Path) -> None:
    workspaces, _search = _setup(tmp_path / "state.sqlite3")
    public = _append(workspaces, room_id="room-public", text="public", key="public")
    _append(workspaces, room_id="room-private", text="private", key="private")
    result = workspaces.query_events(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-b",
        filters={"event_type": "message.posted", "occurred_before": public["occurred_at"] + 1},
        limit=10,
    )
    assert [item["room_id"] for item in result["items"]] == ["room-public"]


def test_codecompass_metadata_is_digest_bound_and_context_only_reduces_scope(tmp_path: Path) -> None:
    workspaces, search = _setup(tmp_path / "state.sqlite3")
    event = build_event(
        workspace_id="workspace-a",
        room_id="room-public",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={
            "text": "symbol needle",
            "codecompass": {
                "source_id": "source-a",
                "source_digest": "a" * 64,
                "symbol_id": "symbol-a",
                "graph_ref": "graph-a",
                "graph_digest": "b" * 64,
                "index_run_id": "index-run-a",
                "completeness": "partial",
            },
        },
        idempotency_key="codecompass",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=event,
    )
    search.rebuild("tenant-a", "workspace-a")
    result = search.code_context(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        query="needle",
        allowed_source_ids={"source-a", "source-b"},
        room_source_ids={"source-a"},
        task_source_ids={"source-a", "source-c"},
    )
    assert result["effective_source_ids"] == ["source-a"]
    assert result["scope_broadened"] is False
    assert result["items"][0]["codecompass"]["graph_digest"] == "b" * 64
    assert result["coverage_notice"] == "codecompass_partial_or_unavailable"
