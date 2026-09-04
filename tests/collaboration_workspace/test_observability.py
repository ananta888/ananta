from __future__ import annotations

import json
from pathlib import Path

from agent.services.collaboration_observability_service import CollaborationObservabilityService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, build_event, room, service


def test_observability_is_content_free_bounded_and_alerts_on_lag(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Observable",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=build_event(
            workspace_id="workspace-a",
            room_id="room-main",
            actor_binding_id="human-user-a",
            event_type="message.posted",
            payload={"text": "private message marker"},
            idempotency_key="message-a",
        ),
    )
    result = CollaborationObservabilityService(
        CollaborationWorkspaceStore(database),
        thresholds={"outbox_pending": 0, "outbox_retry": 0, "projection_lag": 0, "search_lag": 0},
    ).snapshot("tenant-a", "workspace-a")

    assert result["healthy"] is False
    assert {item["signal"] for item in result["alerts"]} == {
        "outbox_pending",
        "projection_lag",
        "search_lag",
    }
    serialized = json.dumps(result)
    assert "private message marker" not in serialized
    assert "actor-a" not in serialized
    assert "room-main" not in serialized
