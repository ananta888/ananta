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


def test_room_conversation_history_filters_by_session_id(client):
    import agent.routes.snakes_execution_routes as ser
    from agent.routes.snakes import _room_messages

    s1 = _register(client, "SessionContextSnake")
    _room_messages.extend(
        [
            {
                "sender_id": s1["id"],
                "sender_kind": "user",
                "text": "Frage in A",
                "session_id": "session-a",
            },
            {
                "sender_id": "ai-snake",
                "sender_kind": "assistant",
                "text": "Antwort in A",
                "session_id": "session-a",
            },
            {
                "sender_id": s1["id"],
                "sender_kind": "user",
                "text": "Frage in B",
                "session_id": "session-b",
            },
            {
                "sender_id": s1["id"],
                "sender_kind": "user",
                "text": "Folgefrage in A",
                "session_id": "session-a",
            },
        ]
    )

    history = ser._build_room_conversation_history(
        snake_id=s1["id"],
        current_text="Folgefrage in A",
        session_id="session-a",
    )

    assert history == [
        {"role": "user", "content": "Frage in A"},
        {"role": "assistant", "content": "Antwort in A"},
    ]


def test_append_room_ai_message_does_not_apply_hidden_storage_cut(client):
    import agent.routes.snakes_execution_routes as ser
    from agent.routes.snakes import _room_messages

    del client
    text = "A" * 9000

    ser._append_room_ai_message(text=text)

    assert _room_messages[-1]["text"] == text


def test_fit_answer_to_chars_marks_last_resort_truncation(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser

    del client
    monkeypatch.setattr(ser, "generate_text", lambda **kwargs: "B" * 1500)

    stored = ser._fit_answer_to_chars(
        "B" * 1500,
        limit=1000,
        provider="lmstudio",
        model=None,
        overflow_policy="truncate",
    )

    assert len(stored) <= 1000
    assert stored.endswith("[gekuerzt]")


def test_fit_answer_to_chars_allows_overlong_answer_by_default(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser

    del client
    monkeypatch.setattr(ser, "generate_text", lambda **kwargs: "short")

    stored = ser._fit_answer_to_chars("C" * 1500, limit=1000, provider="lmstudio", model=None)

    assert stored == "C" * 1500


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
    monkeypatch.setattr(rps, "_is_rag_iterative_intent", lambda *a, **kw: False)
    monkeypatch.setattr(ser, "_pick_worker_for_ask", lambda: ("http://worker.test", "tok"))
    monkeypatch.setattr(ser, "_resolve_lmstudio_model_for_worker", lambda model: model)
    monkeypatch.setattr(ser, "_resolve_ai_snake_chat_provider", lambda: ("lmstudio", "hub-model", None))

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
    assert data["answer"] == "x" * 900
    payload = captured["payload"]
    assert payload["model"] == "request-model"
    assert payload["max_tokens"] == 700
    assert payload["max_context_chars"] == 5000
    assert payload["answer_chars"] == 800
    assert payload["answer_overflow_policy"] == "allow"
    assert payload["never_truncate_answers"] is True
    assert data["trace"]["rag"]["context_chars"] == 5000
    assert data["trace"]["worker"]["limits"]["rag_top_k"] == 9


def test_snake_ask_applies_limits_to_hub_fallback(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser
    import agent.services.retrieval_profile_service as rps

    captured_calls: list[dict[str, object]] = []

    monkeypatch.setattr(rps, "_is_full_scan_intent", lambda *a, **kw: False)
    monkeypatch.setattr(rps, "_is_rag_iterative_intent", lambda *a, **kw: False)
    monkeypatch.setattr(ser, "_worker_propose", lambda *args, **kwargs: ("", {"error": "test"}))
    monkeypatch.setattr(ser, "_resolve_ai_snake_chat_provider", lambda: ("lmstudio", "hub-model", None))

    def _fake_generate_text(**kwargs):
        captured_calls.append(dict(kwargs))
        return "y" * 900

    monkeypatch.setattr(ser, "generate_text", _fake_generate_text)

    resp = client.post(
        "/snake/ask",
        json={
            "question": "hi",
            "context": "context",
            "answer_chars": 700,
            "answer_overflow_policy": "truncate",
            "never_truncate_answers": False,
            "max_tokens": 650,
            "debug": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["path"] == "hub_direct"
    assert data["answer"].endswith("[gekuerzt]")
    assert len(data["answer"]) < 730
    assert captured_calls[0]["max_output_tokens"] == 650
    assert "maximal 700 Zeichen" in str(captured_calls[0]["prompt"])
    assert len(captured_calls) == 1


def test_snake_ask_summarizes_overlong_hub_answer_before_truncating(client, monkeypatch):
    import agent.routes.snakes_execution_routes as ser
    import agent.services.retrieval_profile_service as rps

    calls: list[dict[str, object]] = []

    monkeypatch.setattr(rps, "_is_full_scan_intent", lambda *a, **kw: False)
    monkeypatch.setattr(rps, "_is_rag_iterative_intent", lambda *a, **kw: False)
    monkeypatch.setattr(ser, "_worker_propose", lambda *args, **kwargs: ("", {"error": "test"}))
    monkeypatch.setattr(ser, "_resolve_ai_snake_chat_provider", lambda: ("lmstudio", "hub-model", None))

    def _fake_generate_text(**kwargs):
        calls.append(dict(kwargs))
        return "z" * 900 if len(calls) == 1 else "kurze zusammenfassung"

    monkeypatch.setattr(ser, "generate_text", _fake_generate_text)

    resp = client.post(
        "/snake/ask",
        json={
            "question": "hi",
            "context": "context",
            "answer_chars": 700,
            "answer_overflow_policy": "summarize",
            "max_tokens": 650,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["answer"] == "kurze zusammenfassung"
    assert len(calls) == 2


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


def test_room_message_persists_client_session_id(client):
    from agent.routes.snakes import _room_messages

    s1 = _register(client, "SessionSender")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={
            "channel_type": "room",
            "text": "session scoped",
            "visibility": "room",
            "session_id": "session-a",
        },
        headers={"Authorization": f"Bearer {s1['token']}"},
    )

    assert resp.status_code == 202
    assert _room_messages[-1]["session_id"] == "session-a"


def test_receive_room_messages_can_filter_by_session_id(client):
    s1 = _register(client, "ScopedSender")
    s2 = _register(client, "ScopedReceiver")
    for sid, text in (("session-a", "only a"), ("session-b", "only b")):
        client.post(
            f"/snakes/{s1['id']}/chat/messages",
            json={
                "channel_type": "room",
                "text": text,
                "visibility": "room",
                "session_id": sid,
            },
            headers={"Authorization": f"Bearer {s1['token']}"},
        )

    resp = client.get(f"/snakes/{s2['id']}/chat/messages?session_id=session-a")
    assert resp.status_code == 200
    texts = [m["text"] for m in resp.get_json().get("messages", [])]
    assert "only a" in texts
    assert "only b" not in texts


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


# ── ananta-visual log session (read-only) ─────────────────────────────────────


def test_ananta_visual_default_session_is_read_only():
    """The built-in ananta-visual session must be seeded as a read-only log."""
    from client_surfaces.operator_tui.chat_state import default_sessions
    sessions = default_sessions()
    visual = next((s for s in sessions if s.get("id") == "ananta-visual"), None)
    assert visual is not None, "ananta-visual session must be in default_sessions()"
    assert visual["group"] == "Konfiguration"
    assert visual["settings"]["chat_read_only"] is True
    assert visual["settings"]["chat_backend"] == "ananta-worker"


def test_ananta_visual_session_is_added_to_legacy_state():
    """Existing user.json state must have ananta-visual backfilled on load."""
    from client_surfaces.operator_tui.chat_state import get_sessions
    chat: dict = {}  # legacy / empty state
    sessions = get_sessions(chat)
    visual = next((s for s in sessions if s.get("id") == "ananta-visual"), None)
    assert visual is not None


def test_chat_read_only_default_is_false():
    """Non-built-in sessions should not be read-only by default."""
    from client_surfaces.operator_tui.chat_state import make_session
    sess = make_session(session_id="my-custom", name="Custom")
    assert sess["settings"]["chat_read_only"] is False


def test_send_to_ananta_visual_session_is_rejected(client):
    """User posts to the ananta-visual session must be rejected with 403.
    Only the backend's [ui-tick] system path is allowed to write to it."""
    s1 = _register(client, "VisualUser")
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={
            "channel_type": "room",
            "text": "Ich poste in den Visual-Log",
            "visibility": "room",
            "session_id": "ananta-visual",
        },
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 403
    assert "Read-only" in resp.get_json().get("error", "")


def test_ui_tick_to_ananta_visual_is_accepted_and_logged(client):
    """The [ui-tick] system path must be accepted and append a system message
    to _room_messages with session_id='ananta-visual'."""
    from agent.routes.snakes import _room_messages
    _room_messages.clear()
    s1 = _register(client, "VisualSnake")
    snapshot = "/teams | nav:Teams* | h:Blueprints | list:3"
    resp = client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={
            "channel_type": "room",
            "text": f"[ui-tick] {snapshot}",
            "visibility": "system",
            "session_id": "ananta-visual",
            "ui_context": {
                "route": "/teams",
                "visible_waypoints": ["nav./teams", "teams.tab-blueprints"],
                "ui_snapshot": snapshot,
            },
        },
        headers={"Authorization": f"Bearer {s1['token']}"},
    )
    assert resp.status_code == 202
    # The tick should be persisted as a system message in the visual session
    visual_msgs = [m for m in _room_messages if m.get("session_id") == "ananta-visual"]
    assert len(visual_msgs) == 1
    assert visual_msgs[0]["visibility"] == "system"
    assert visual_msgs[0]["sender_id"] == "browser"
    assert visual_msgs[0]["text"].startswith("[ui-tick]")
    assert visual_msgs[0].get("ui_snapshot") == snapshot


def test_append_room_ai_message_sets_visibility_and_sender():
    """_append_room_ai_message must honour visibility/sender_id/ui_snapshot overrides."""
    from agent.routes.snakes import _room_messages
    from agent.routes.snakes_execution_routes import _append_room_ai_message
    _room_messages.clear()
    _append_room_ai_message(
        text="hallo",
        session_id="x",
        visibility="system",
        sender_id="browser",
        ui_snapshot="snap",
    )
    assert len(_room_messages) == 1
    m = _room_messages[0]
    assert m["visibility"] == "system"
    assert m["sender_id"] == "browser"
    assert m["sender_kind"] == "system"
    assert m["session_id"] == "x"
    assert m["ui_snapshot"] == "snap"
