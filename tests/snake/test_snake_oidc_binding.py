from __future__ import annotations

import time

import jwt
import pytest


def _user_jwt(username: str, tenant_id: str | None = None) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            **({"tenant_id": tenant_id} if tenant_id else {}),
            "role": "user",
            "iat": now,
            "exp": now + 1800,
        },
        settings.secret_key,
        algorithm="HS256",
    )


@pytest.fixture
def app():
    from flask import Flask

    from agent.routes.snakes import _chat_messages, _messages, _room_messages, _snakes, snakes_bp

    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snakes_bp)
    _snakes.clear()
    _messages.clear()
    _chat_messages.clear()
    _room_messages.clear()
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def test_register_snake_ignores_spoofed_oidc_id_from_body(client):
    token = _user_jwt("alice")
    response = client.post(
        "/snakes",
        headers={"Authorization": f"Bearer {token}", "X-Ananta-Device-Id": "dev-a"},
        json={"name": "AliceSnake", "role": "player", "oidc_id": "mallory"},
    )
    assert response.status_code == 201
    snake_id = response.get_json()["id"]

    from agent.routes.snakes import _snakes

    snake = dict(_snakes.get(snake_id) or {})
    assert snake.get("oidc_id") == "alice"
    assert snake.get("oidc_id") != "mallory"
    assert snake.get("auth_mode") == "user_jwt"


@pytest.mark.parametrize("remote_addr", ["8.8.8.8", "172.18.0.4", "192.168.1.20", "10.0.0.8"])
def test_register_snake_requires_login_for_remote_requests(client, remote_addr):
    response = client.post(
        "/snakes",
        environ_base={"REMOTE_ADDR": remote_addr},
        json={"name": "NoLogin", "role": "viewer"},
    )
    assert response.status_code == 401


def test_register_snake_requires_login_for_loopback_by_default(client):
    response = client.post(
        "/snakes",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        json={"name": "NoImplicitLocalTrust", "role": "viewer"},
    )
    assert response.status_code == 401


def test_explicit_dev_bypass_accepts_only_true_loopback(client, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "snake_local_dev_auth_bypass", True)
    monkeypatch.setattr(settings, "workflow_require_registered_worker_auth", False)

    loopback = client.post(
        "/snakes",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        json={"name": "ExplicitLocalDev", "role": "viewer"},
    )
    docker_bridge = client.post(
        "/snakes",
        environ_base={"REMOTE_ADDR": "172.18.0.4"},
        json={"name": "DockerIsRemote", "role": "viewer"},
    )

    assert loopback.status_code == 201
    assert docker_bridge.status_code == 401


def test_production_profile_disables_explicit_dev_bypass(client, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "snake_local_dev_auth_bypass", True)
    monkeypatch.setattr(settings, "workflow_require_registered_worker_auth", True)

    response = client.post(
        "/snakes",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        json={"name": "ProductionLoopback", "role": "viewer"},
    )

    assert response.status_code == 401


def test_register_snake_accepts_strict_service_bearer(app, client):
    from agent.config import settings

    service_token = "snake-service-token-with-at-least-32-bytes"
    app.config["AGENT_TOKEN"] = service_token

    response = client.post(
        "/snakes",
        headers={"Authorization": f"Bearer {service_token}"},
        json={"name": "ServiceSnake", "role": "viewer"},
    )

    assert response.status_code == 201
    from agent.routes.snakes import _snakes

    stored = _snakes[response.get_json()["id"]]
    assert stored["owner_principal"] == {
        "tenant_id": "service:ananta",
        "subject_id": f"service:{settings.agent_name}",
    }


def test_snake_ask_accepts_strict_service_bearer(app, client):
    service_token = "snake-ask-service-token-with-at-least-32-bytes"
    app.config["AGENT_TOKEN"] = service_token

    response = client.post(
        "/snake/ask",
        headers={"Authorization": f"Bearer {service_token}"},
        json={"question": "Where is TaskRouter?", "trace_only": True},
    )

    assert response.status_code == 200
    assert response.get_json()["trace_only"] is True


def test_snake_ask_rejects_unauthenticated_private_network_caller(client):
    response = client.post(
        "/snake/ask",
        environ_base={"REMOTE_ADDR": "172.18.0.8"},
        json={"question": "Where is TaskRouter?", "trace_only": True},
    )

    assert response.status_code == 401


def test_chat_send_rejects_user_mismatch_and_requires_owned_session(client, monkeypatch):
    from agent.routes import snakes_execution_handlers as handlers

    alice_token = _user_jwt("alice")
    bob_token = _user_jwt("bob")

    created = client.post(
        "/snakes",
        headers={"Authorization": f"Bearer {alice_token}", "X-Ananta-Device-Id": "alice-dev"},
        json={"name": "Alice", "role": "player"},
    )
    assert created.status_code == 201
    snake = created.get_json()
    snake_id = snake["id"]
    snake_token = snake["token"]

    monkeypatch.setattr(
        handlers,
        "_owned_chat_session_snapshot",
        lambda session_id, principal: (
            {"id": session_id, "settings": {}, "profile_id": "general"}
            if session_id == "session-a"
            and (principal.tenant_id, principal.subject_id) == ("alice", "alice")
            else None
        ),
    )

    spoof = client.post(
        f"/snakes/{snake_id}/chat/messages",
        headers={"Authorization": f"Bearer {snake_token}", "X-Ananta-Device-Id": "alice-dev", "X-Ananta-User-Authorization": f"Bearer {bob_token}"},
        json={
            "channel_type": "room",
            "text": "spoof",
            "visibility": "room",
            "session_id": "session-a",
        },
    )
    assert spoof.status_code == 404

    sessionless = client.post(
        f"/snakes/{snake_id}/chat/messages",
        headers={
            "Authorization": f"Bearer {snake_token}",
            "X-Ananta-Device-Id": "alice-dev",
            "X-Ananta-User-Authorization": f"Bearer {alice_token}",
        },
        json={"channel_type": "room", "text": "ok", "visibility": "room"},
    )
    valid = client.post(
        f"/snakes/{snake_id}/chat/messages",
        headers={
            "Authorization": f"Bearer {snake_token}",
            "X-Ananta-Device-Id": "alice-dev",
            "X-Ananta-User-Authorization": f"Bearer {alice_token}",
        },
        json={
            "channel_type": "room",
            "text": "ok",
            "visibility": "room",
            "session_id": "session-a",
        },
    )
    assert sessionless.status_code == 400
    assert sessionless.get_json()["error_code"] == "chat_session_required"
    assert valid.status_code == 202


def test_message_reads_are_exactly_tenant_session_bound_and_non_draining(client, monkeypatch):
    from agent.routes import snakes_execution_handlers as handlers

    alice_token = _user_jwt("shared-user", "tenant-a")
    foreign_token = _user_jwt("shared-user", "tenant-b")
    created = client.post(
        "/snakes",
        headers={"Authorization": f"Bearer {alice_token}", "X-Ananta-Device-Id": "device-a"},
        json={"name": "Alice", "role": "player"},
    ).get_json()

    def owned_snapshot(session_id, principal):
        if session_id == "session-a" and (principal.tenant_id, principal.subject_id) == (
            "tenant-a",
            "shared-user",
        ):
            return {"id": session_id, "settings": {}, "profile_id": "general"}
        return None

    monkeypatch.setattr(handlers, "_owned_chat_session_snapshot", owned_snapshot)
    sent = client.post(
        f"/snakes/{created['id']}/chat/messages",
        headers={
            "Authorization": f"Bearer {created['token']}",
            "X-Ananta-User-Authorization": f"Bearer {alice_token}",
            "X-Ananta-Device-Id": "device-a",
        },
        json={
            "channel_type": "room",
            "visibility": "room",
            "text": "private",
            "session_id": "session-a",
        },
    )
    unauthenticated = client.get(
        f"/snakes/{created['id']}/chat/messages?session_id=session-a"
    )
    foreign = client.get(
        f"/snakes/{created['id']}/chat/messages?session_id=session-a",
        headers={
            "Authorization": f"Bearer {foreign_token}",
            "X-Ananta-Device-Id": "device-a",
        },
    )
    headers = {
        "Authorization": f"Bearer {alice_token}",
        "X-Ananta-Device-Id": "device-a",
    }
    first = client.get(
        f"/snakes/{created['id']}/chat/messages?session_id=session-a",
        headers=headers,
    )
    second = client.get(
        f"/snakes/{created['id']}/chat/messages?session_id=session-a",
        headers=headers,
    )

    assert sent.status_code == 202
    assert unauthenticated.status_code == 401
    assert foreign.status_code == 404
    assert [item["text"] for item in first.get_json()["messages"]] == ["private"]
    assert second.get_json()["messages"] == first.get_json()["messages"]
    assert "owner_principal" not in first.get_json()["messages"][0]


def test_direct_message_cannot_target_foreign_principal(client, monkeypatch):
    from agent.routes import snakes_execution_handlers as handlers

    alice_token = _user_jwt("alice", "tenant-a")
    bob_token = _user_jwt("bob", "tenant-b")
    alice = client.post(
        "/snakes",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"name": "Alice", "role": "player"},
    ).get_json()
    bob = client.post(
        "/snakes",
        headers={"Authorization": f"Bearer {bob_token}"},
        json={"name": "Bob", "role": "player"},
    ).get_json()
    monkeypatch.setattr(
        handlers,
        "_owned_chat_session_snapshot",
        lambda session_id, principal: (
            {"id": session_id, "settings": {}, "profile_id": "general"}
            if session_id == "session-a"
            and (principal.tenant_id, principal.subject_id) == ("tenant-a", "alice")
            else None
        ),
    )

    response = client.post(
        f"/snakes/{alice['id']}/chat/messages",
        headers={
            "Authorization": f"Bearer {alice['token']}",
            "X-Ananta-User-Authorization": f"Bearer {alice_token}",
        },
        json={
            "channel_type": "direct",
            "visibility": "direct",
            "target_ids": [bob["id"]],
            "text": "must not arrive",
            "session_id": "session-a",
        },
    )

    assert response.status_code == 404


def test_snake_control_and_legacy_messages_are_exactly_owner_bound(client):
    alice_token = _user_jwt("shared", "tenant-a")
    foreign_token = _user_jwt("shared", "tenant-b")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    foreign_headers = {"Authorization": f"Bearer {foreign_token}"}
    created = client.post(
        "/snakes",
        headers=alice_headers,
        json={"name": "Alice", "role": "player"},
    ).get_json()

    assert client.get("/snakes").status_code == 401
    assert client.delete(f"/snakes/{created['id']}").status_code == 401
    foreign_list = client.get("/snakes", headers=foreign_headers)
    assert foreign_list.status_code == 200
    assert foreign_list.get_json()["snakes"] == []
    assert client.delete(
        f"/snakes/{created['id']}",
        headers=foreign_headers,
    ).status_code == 404

    legacy_send = client.post(
        f"/snakes/{created['id']}/messages",
        headers=alice_headers,
        json={"from_id": created["id"], "text": "private legacy message"},
    )
    assert legacy_send.status_code == 202
    assert client.get(
        f"/snakes/{created['id']}/messages",
        headers=foreign_headers,
    ).status_code == 404
    legacy_read = client.get(
        f"/snakes/{created['id']}/messages",
        headers=alice_headers,
    )
    assert legacy_read.status_code == 200
    assert legacy_read.get_json()["messages"][0]["text"] == "private legacy message"
    assert "owner_principal" not in legacy_read.get_json()["messages"][0]

    owned_list = client.get("/snakes", headers=alice_headers)
    assert [item["id"] for item in owned_list.get_json()["snakes"]] == [created["id"]]
    assert client.delete(
        f"/snakes/{created['id']}",
        headers=alice_headers,
    ).status_code == 200
