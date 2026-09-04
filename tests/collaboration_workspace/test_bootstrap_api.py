from __future__ import annotations

from flask import Flask

from agent.bootstrap.collaboration_workspace import initialize_collaboration_workspace
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import build_event, room


def _wire(app: Flask, tmp_path, *, enabled: bool = True, allowed_tools: str = "") -> None:
    app.config.update(
        ROLE="hub",
        ANANTA_COLLABORATION_WORKSPACE_ENABLED=enabled,
        ANANTA_COLLABORATION_WORKSPACE_STATE=str(tmp_path / "collaboration.sqlite3"),
        ANANTA_COLLABORATION_AUTO_APPROVED_TOOLS=allowed_tools,
        ANANTA_COLLABORATION_COMMAND_POLICY_REVISION=3,
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


def test_composition_uses_runtime_role_when_flask_config_has_no_role(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("agent.bootstrap.collaboration_workspace.settings.role", "hub")
    app = Flask("runtime-hub")
    app.config.update(
        ANANTA_COLLABORATION_WORKSPACE_ENABLED=True,
        ANANTA_COLLABORATION_WORKSPACE_STATE=str(tmp_path / "collaboration.sqlite3"),
    )

    status = initialize_collaboration_workspace(app)

    assert status.ready is True
    assert "collaboration_workspace_service" in app.extensions


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


def test_command_api_has_fully_automatic_approved_and_blocked_paths(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path, allowed_tools="pytest")
    workspace = client.post(
        "/api/collaboration/workspaces",
        headers=admin_auth_header,
        json={"workspace_id": "workspace-command", "title": "Commands", "display_name": "Admin"},
    ).get_json()["data"]
    client.post(
        "/api/collaboration/workspaces/workspace-command/rooms",
        headers=admin_auth_header,
        json=room(),
    )

    def request(request_id: str, tool_id: str):
        return {
            "request_id": request_id,
            "workspace_id": "workspace-command",
            "room_id": "room-main",
            "actor_binding_id": workspace["created_by"],
            "task_id": "task-a",
            "tool_id": tool_id,
            "operation": "execute",
            "plan_digest": canonical_digest({"command": tool_id}),
            "artifact_digest": None,
            "policy_revision": 3,
        }

    approved = client.post(
        "/api/collaboration/workspaces/workspace-command/command-decisions",
        headers=admin_auth_header,
        json=request("request-approved", "pytest"),
    )
    blocked = client.post(
        "/api/collaboration/workspaces/workspace-command/command-decisions",
        headers=admin_auth_header,
        json=request("request-blocked", "shell"),
    )
    assert approved.status_code == 201
    assert approved.get_json()["data"]["state"] == "approved"
    assert blocked.status_code == 201
    assert blocked.get_json()["data"]["state"] == "blocked"
    assert blocked.get_json()["data"]["human_intervention_required"] is False
