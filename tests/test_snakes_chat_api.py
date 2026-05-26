"""T07.02: Integrationstests für Snake Chat API (Hub-Endpunkte)."""
from __future__ import annotations

import json
import pytest


@pytest.fixture
def app():
    from flask import Flask
    from agent.routes.snakes import snakes_bp, _snakes, _messages, _chat_messages, _room_messages
    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snakes_bp)
    # Reset in-memory stores before each test
    _snakes.clear()
    _messages.clear()
    _chat_messages.clear()
    _room_messages.clear()
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _register(client, name="TestSnake", role="player"):
    resp = client.post("/snakes", json={"name": name, "role": role})
    assert resp.status_code == 201
    return resp.get_json()


# ── Registration ─────────────────────────────────────────────────────────────


def test_register_snake_returns_id_token_color(client):
    data = _register(client)
    assert "id" in data
    assert "token" in data
    assert "color" in data


def test_register_snake_max_active(client):
    for i in range(8):
        resp = client.post("/snakes", json={"name": f"s{i}", "role": "player"})
        assert resp.status_code == 201
    resp = client.post("/snakes", json={"name": "overflow", "role": "player"})
    assert resp.status_code == 409


# ── Chat: send room message ───────────────────────────────────────────────────


def test_send_room_message(client):
    s1 = _register(client, "Alice")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": "hello room", "visibility": "room"},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["ok"] is True


def test_send_room_message_invalid_token(client):
    s1 = _register(client, "Bob")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": "hi", "visibility": "room"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


def test_send_room_message_empty_text_rejected(client):
    s1 = _register(client, "Charlie")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": "", "visibility": "room"},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 400


# ── Chat: local_only rejected ─────────────────────────────────────────────────


def test_local_only_message_rejected(client):
    s1 = _register(client, "Dave")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "notes", "text": "secret", "visibility": "local_only"},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code in {401, 422}  # notes rejected (invalid token or 422)


def test_local_only_visibility_rejected(client):
    s1 = _register(client, "Eve")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": "oops", "visibility": "local_only"},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 422


# ── Chat: direct message ──────────────────────────────────────────────────────


def test_direct_message_between_two_snakes(client):
    s1 = _register(client, "Frank")
    s2 = _register(client, "Grace")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={
            "channel_type": "direct",
            "text": "hi direct",
            "visibility": "direct",
            "target_ids": [s2["id"]],
        },
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 202


def test_direct_message_unknown_target_rejected(client):
    s1 = _register(client, "Heidi")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={
            "channel_type": "direct",
            "text": "hi",
            "visibility": "direct",
            "target_ids": ["s-nonexistent"],
        },
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 422


# ── Chat: receive messages ────────────────────────────────────────────────────


def test_receive_room_messages(client):
    s1 = _register(client, "Ivan")
    s2 = _register(client, "Judy")
    # s1 sends to room
    client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": "greetings", "visibility": "room"},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    # s2 fetches
    resp = client.get(f"/snakes/{s2['id']}/chat/messages")
    assert resp.status_code == 200
    data = resp.get_json()
    texts = [m["text"] for m in data.get("messages", [])]
    assert "greetings" in texts


def test_receive_without_duplicates(client):
    s1 = _register(client, "Karl")
    s2 = _register(client, "Laura")
    client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": "once", "visibility": "room", "id": "fixed-id-001"},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    resp1 = client.get(f"/snakes/{s2['id']}/chat/messages")
    cursor = resp1.get_json().get("cursor", "")
    # Second fetch with cursor should not return same message
    resp2 = client.get(f"/snakes/{s2['id']}/chat/messages?since={cursor}")
    data2 = resp2.get_json()
    ids = [m["id"] for m in data2.get("messages", [])]
    assert "fixed-id-001" not in ids


# ── Chat: ack ─────────────────────────────────────────────────────────────────


def test_ack_messages(client):
    s1 = _register(client, "Mike")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/ack",
        json={"message_ids": ["id-1", "id-2"]},
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["acked"] == 2


def test_ack_invalid_token(client):
    s1 = _register(client, "Nina")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/ack",
        json={"message_ids": []},
        headers={"Authorization": "Bearer bad"},
    )
    assert resp.status_code == 401


# ── Participants ──────────────────────────────────────────────────────────────


def test_participants_list(client):
    s1 = _register(client, "Oscar")
    resp = client.get("/snakes/participants")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = [p["id"] for p in data["participants"]]
    assert s1["id"] in ids
