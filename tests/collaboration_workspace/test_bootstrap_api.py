from __future__ import annotations

from flask import Flask

from agent.bootstrap.collaboration_workspace import initialize_collaboration_workspace
from tests.collaboration_workspace.helpers import build_event, room


def _wire(app: Flask, tmp_path, *, enabled: bool = True) -> None:
    app.config.update(
        ROLE="hub",
        ANANTA_COLLABORATION_WORKSPACE_ENABLED=enabled,
        ANANTA_COLLABORATION_WORKSPACE_STATE=str(tmp_path / "collaboration.sqlite3"),
    )
    initialize_collaboration_workspace(app)


def test_composition_is_hub_only_and_default_off(tmp_path) -> None:
    hub = Flask("hub")
    hub.config["ROLE"] = "hub"
    status = initialize_collaboration_workspace(hub)
    assert status.ready is False
    assert status.reason_code == "collaboration_workspace_disabled"

    worker = Flask("worker")
    worker.config.update(ROLE="worker", ANANTA_COLLABORATION_WORKSPACE_ENABLED=True)
    status = initialize_collaboration_workspace(worker)
    assert status.ready is False
    assert status.reason_code == "collaboration_hub_role_required"


def test_native_api_create_room_event_timeline_and_search_is_headless(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    created = client.post(
        "/api/collaboration/workspaces",
        headers=admin_auth_header,
        json={"workspace_id": "workspace-api", "title": "API Workspace", "display_name": "Admin"},
    )
    assert created.status_code == 201
    workspace = created.get_json()["data"]
    actor_id = workspace["created_by"]
    listed = client.get("/api/collaboration/workspaces", headers=admin_auth_header)
    assert listed.status_code == 200
    assert listed.get_json()["data"]["items"][0]["workspace_id"] == "workspace-api"
    room_response = client.post(
        "/api/collaboration/workspaces/workspace-api/rooms",
        headers=admin_auth_header,
        json=room(),
    )
    assert room_response.status_code == 201
    event = build_event(
        workspace_id="workspace-api",
        room_id="room-main",
        actor_binding_id=actor_id,
        event_type="message.posted",
        payload={"text": "headless collaboration"},
        idempotency_key="api-message-one",
    )
    appended = client.post(
        "/api/collaboration/workspaces/workspace-api/events",
        headers=admin_auth_header,
        json=event,
    )
    assert appended.status_code == 201
    assert appended.get_json()["data"]["human_intervention_required"] is False
    timeline = client.get(
        "/api/collaboration/workspaces/workspace-api/timeline?room_id=room-main",
        headers=admin_auth_header,
    )
    assert timeline.status_code == 200
    assert timeline.get_json()["data"]["items"][0]["payload"]["text"] == "headless collaboration"
    search = client.get(
        "/api/collaboration/workspaces/workspace-api/search?q=headless",
        headers=admin_auth_header,
    )
    assert search.status_code == 200
    assert len(search.get_json()["data"]["items"]) == 1


def test_api_is_tenant_scoped_and_malformed_payload_fails_closed(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    malformed = client.post(
        "/api/collaboration/workspaces",
        headers={**admin_auth_header, "Content-Type": "application/json"},
        data="not-json",
    )
    assert malformed.status_code == 422
    missing = client.get(
        "/api/collaboration/workspaces/missing-workspace",
        headers=admin_auth_header,
    )
    assert missing.status_code == 403
