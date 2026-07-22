from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.webrtc_sfu_broadcast_commands import webrtc_sfu_broadcast_commands_bp
from agent.routes.webrtc_sfu_broadcast_operations import webrtc_sfu_broadcast_operations_bp
from agent.services.sfu_broadcast_command_service import (
    InMemorySfuBroadcastCommandLedger,
    SfuBroadcastCommandAuthorization,
    SfuBroadcastCommandExecution,
    SfuBroadcastCommandService,
)
from agent.services.sfu_broadcast_operations_read_model import (
    InMemorySfuBroadcastOperationsSnapshotPort,
    SfuBroadcastOperationsReadModel,
    SfuBroadcastOperationsRecord,
    SfuBroadcastOperationsSnapshot,
)


class _Authorizer:
    def authorize(self, principal, command):
        return SfuBroadcastCommandAuthorization(command.room_ref in principal.room_scopes, "sfu_command_room_forbidden")


class _Executor:
    def execute(self, principal, command, audit_event):
        return SfuBroadcastCommandExecution(True, command.expected_version + 1, "active", "sfu_broadcast_started", True)


def _record():
    return SfuBroadcastOperationsRecord(
        1000, "tenant-a", "eu-1", "room-a", "user-a", "receiver-a", 10,
        "active", "applied", "current", "sfu", "healthy", "high", "medium", "medium",
        {"medium": 10}, 1, "none", 100, 1000, 0, "converged", "none", "broadcast_25", "observe_only",
    )


def _app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(webrtc_sfu_broadcast_operations_bp)
    app.register_blueprint(webrtc_sfu_broadcast_commands_bp)
    source = InMemorySfuBroadcastOperationsSnapshotPort(SfuBroadcastOperationsSnapshot("snapshot-1", (_record(),)))
    app.extensions["sfu_broadcast_operations_read_model"] = SfuBroadcastOperationsReadModel(
        source=source, diagnostic_secret=b"r" * 32, clock=lambda: 1100
    )
    app.extensions["sfu_broadcast_command_service"] = SfuBroadcastCommandService(
        authorizer=_Authorizer(), executor=_Executor(), ledger=InMemorySfuBroadcastCommandLedger(), diagnostic_secret=b"r" * 32
    )
    return app


def _headers(**claims):
    payload = {"sub": "user-a", "tenant_id": "tenant-a", "role": "user", "room_scopes": ["room-a"]}
    payload.update(claims)
    token = generate_token(payload, settings.secret_key, expires_in=3600)
    return {"Authorization": f"Bearer {token}"}


def test_operations_route_is_authenticated_read_only_and_content_free():
    client = _app().test_client()
    assert client.get("/v1/semantic-media/sfu/broadcast/operations").status_code == 401
    response = client.get("/v1/semantic-media/sfu/broadcast/operations", headers=_headers())
    assert response.status_code == 200
    assert len(response.get_json()["items"]) == 1
    assert "room-a" not in response.get_data(as_text=True)
    assert client.get("/v1/semantic-media/sfu/broadcast/operations?payload=forged", headers=_headers()).status_code == 400
    assert client.post("/v1/semantic-media/sfu/broadcast/operations", headers=_headers()).status_code == 405


def test_command_route_requires_idempotency_expected_version_and_hub_confirmation():
    client = _app().test_client()
    body = {"room_ref": "room-a", "command": "start", "expected_version": 2, "confirmed": True, "options": {}}
    missing = client.post("/v1/semantic-media/sfu/broadcast/commands", json=body, headers=_headers())
    assert missing.status_code == 400
    headers = {**_headers(), "Idempotency-Key": "route-command-0001"}
    first = client.post("/v1/semantic-media/sfu/broadcast/commands", json=body, headers=headers)
    second = client.post("/v1/semantic-media/sfu/broadcast/commands", json=body, headers=headers)
    assert first.status_code == 200
    assert first.get_json()["effective_version"] == 3
    assert second.get_json()["replayed"] is True
    denied = client.post(
        "/v1/semantic-media/sfu/broadcast/commands",
        json={**body, "room_ref": "room-b"},
        headers={**_headers(), "Idempotency-Key": "route-command-0002"},
    )
    assert denied.status_code == 403
    assert denied.get_json()["reason_code"] == "sfu_command_room_forbidden"

    invalid_version = client.post(
        "/v1/semantic-media/sfu/broadcast/commands",
        json={**body, "expected_version": "2"},
        headers={**_headers(), "Idempotency-Key": "route-command-0003"},
    )
    assert invalid_version.status_code == 400
    assert invalid_version.get_json()["reason_code"] == "sfu_command_expected_version_invalid"
    invalid_action = client.post(
        "/v1/semantic-media/sfu/broadcast/commands",
        json={**body, "command": {"start": True}},
        headers={**_headers(), "Idempotency-Key": "route-command-0004"},
    )
    assert invalid_action.status_code == 400
    assert invalid_action.get_json()["reason_code"] == "sfu_command_invalid"
