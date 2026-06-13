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


def test_room_conversation_history_excludes_current_turn(client):
    import agent.routes.snakes_execution_routes as ser
    from agent.routes.snakes import _room_messages

    s1 = _register(client, "ContextSnake")
    _room_messages.extend(
        [
            {
                "sender_id": s1["id"],
                "sender_kind": "user",
                "text": "Was ist CodeCompass?",
            },
            {
                "sender_id": "ai-snake",
                "sender_kind": "assistant",
                "text": "CodeCompass ist das RAG-System.",
            },
            {
                "sender_id": s1["id"],
                "sender_kind": "user",
                "text": "Und welche Komponenten gehoeren dazu?",
            },
        ]
    )

    history = ser._build_room_conversation_history(
        snake_id=s1["id"],
        current_text="Und welche Komponenten gehoeren dazu?",
    )

    assert history == [
        {"role": "user", "content": "Was ist CodeCompass?"},
        {"role": "assistant", "content": "CodeCompass ist das RAG-System."},
    ]


def test_append_room_ai_message_does_not_silently_cut_at_6000(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser
    from agent.routes.snakes import _room_messages

    del client
    monkeypatch.setattr(ser, "_chat_answer_chars_limit", lambda: 6000)
    text = "A" * 7000

    ser._append_room_ai_message(text=text)

    assert _room_messages[-1]["text"] == text


def test_append_room_ai_message_marks_truncation(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser
    from agent.routes.snakes import _room_messages

    del client
    monkeypatch.setattr(ser, "_room_ai_message_chars_limit", lambda: 1000)

    ser._append_room_ai_message(text="B" * 1500)

    stored = _room_messages[-1]["text"]
    assert len(stored) <= 1000
    assert stored.endswith("[gekuerzt]")


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


def test_snake_ask_forwards_v2_limits_to_worker(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser
    import agent.services.retrieval_profile_service as rps

    captured: dict[str, object] = {}

    monkeypatch.setattr(rps, "_is_full_scan_intent", lambda *a, **kw: False)
    monkeypatch.setattr(ser, "_pick_worker_for_ask", lambda: ("http://worker.test", "tok"))
    monkeypatch.setattr(ser, "_resolve_lmstudio_model_for_worker", lambda model: model)
    monkeypatch.setattr(ser, "_resolve_ai_snake_chat_provider", lambda: ("lmstudio", "hub-model"))

    def _fake_forward(worker_url, path, payload, token=None):
        captured["worker_url"] = worker_url
        captured["path"] = path
        captured["payload"] = dict(payload)
        captured["token"] = token
        return {"data": {"answer": "x" * 900}}

    monkeypatch.setattr("agent.services.task_runtime_service.forward_to_worker", _fake_forward)

    resp = client.post(
        "/snake/ask",
        json={
            "question": "hi",
            "context": "c" * 6000,
            "model": "request-model",
            "context_chars": 5000,
            "answer_chars": 800,
            "max_tokens": 700,
            "rag_top_k": 9,
            "debug": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["path"] == "worker"
    assert data["answer"].endswith("[gekuerzt]")
    assert len(data["answer"]) < 830
    payload = captured["payload"]
    assert payload["model"] == "request-model"
    assert payload["max_tokens"] == 700
    assert payload["max_context_chars"] == 5000
    assert data["trace"]["rag"]["context_chars"] == 5000
    assert data["trace"]["worker"]["limits"]["rag_top_k"] == 9


def test_snake_ask_applies_limits_to_hub_fallback(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser
    import agent.services.retrieval_profile_service as rps

    captured: dict[str, object] = {}

    monkeypatch.setattr(rps, "_is_full_scan_intent", lambda *a, **kw: False)
    monkeypatch.setattr(ser, "_worker_propose", lambda *args, **kwargs: ("", {"error": "test"}))
    monkeypatch.setattr(ser, "_resolve_ai_snake_chat_provider", lambda: ("lmstudio", "hub-model"))

    def _fake_generate_text(**kwargs):
        captured.update(kwargs)
        return "y" * 900

    monkeypatch.setattr(ser, "generate_text", _fake_generate_text)

    resp = client.post(
        "/snake/ask",
        json={
            "question": "hi",
            "context": "context",
            "answer_chars": 700,
            "max_tokens": 650,
            "debug": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["path"] == "hub_direct"
    assert data["answer"].endswith("[gekuerzt]")
    assert len(data["answer"]) < 730
    assert captured["max_output_tokens"] == 650


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
