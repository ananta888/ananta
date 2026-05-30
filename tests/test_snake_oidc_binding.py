from __future__ import annotations

import time

import jwt
import pytest


def _user_jwt(username: str) -> str:
    from agent.config import settings

    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
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


def test_register_snake_requires_login_for_non_local_requests(client):
    response = client.post(
        "/snakes",
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
        json={"name": "NoLogin", "role": "viewer"},
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "oidc_login_required_or_local_dev_only"


def test_chat_send_rejects_user_mismatch_and_accepts_valid_login(client):
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

    spoof = client.post(
        f"/snakes/{snake_id}/chat/messages",
        headers={"Authorization": f"Bearer {snake_token}", "X-Ananta-Device-Id": "alice-dev", "X-Ananta-User-Authorization": f"Bearer {bob_token}"},
        json={"channel_type": "room", "text": "spoof", "visibility": "room"},
    )
    assert spoof.status_code == 403

    valid = client.post(
        f"/snakes/{snake_id}/chat/messages",
        headers={
            "Authorization": f"Bearer {snake_token}",
            "X-Ananta-Device-Id": "alice-dev",
            "X-Ananta-User-Authorization": f"Bearer {alice_token}",
        },
        json={"channel_type": "room", "text": "ok", "visibility": "room"},
    )
    assert valid.status_code == 202
