"""Integration tests für AI-Snake Trace API Endpunkte."""

from __future__ import annotations

import pytest


@pytest.fixture()
def app():
    from flask import Flask
    from agent.routes.snakes import snakes_bp, _snakes, _messages, _chat_messages, _room_messages
    import agent.routes.snakes_execution_routes  # noqa: F401 — registers routes on snakes_bp

    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snakes_bp)

    _snakes.clear()
    _messages.clear()
    _chat_messages.clear()
    _room_messages.clear()

    # Reset trace store
    import agent.routes.ai_snake_trace_store as _ts
    _ts.reset_store_for_testing()

    return a


@pytest.fixture()
def client(app):
    return app.test_client()


def _register(client, name="TraceSnake", role="viewer"):
    resp = client.post("/snakes", json={"name": name, "role": role})
    assert resp.status_code == 201
    return resp.get_json()


# ── Auth / 404 behaviour ──────────────────────────────────────────────────────


def test_list_traces_unknown_snake_returns_404(client):
    resp = client.get("/snakes/nonexistent/chat/traces")
    assert resp.status_code == 404


def test_trace_detail_unknown_snake_returns_404(client):
    resp = client.get("/snakes/nonexistent/chat/traces/abc")
    assert resp.status_code == 404


def test_trace_events_unknown_snake_returns_404(client):
    resp = client.get("/snakes/nonexistent/chat/traces/abc/events")
    assert resp.status_code == 404


def test_trace_detail_unknown_trace_id_returns_404(client):
    snake = _register(client)
    resp = client.get(f"/snakes/{snake['id']}/chat/traces/does-not-exist")
    assert resp.status_code == 404


def test_trace_events_unknown_trace_id_returns_404(client):
    snake = _register(client)
    resp = client.get(f"/snakes/{snake['id']}/chat/traces/does-not-exist/events")
    assert resp.status_code == 404


# ── List traces ───────────────────────────────────────────────────────────────


def test_list_traces_empty_for_new_snake(client):
    snake = _register(client)
    resp = client.get(f"/snakes/{snake['id']}/chat/traces")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["traces"] == []
    assert data["snake_id"] == snake["id"]


def test_list_traces_returns_existing_trace(client):
    import agent.routes.ai_snake_trace_store as _ts
    snake = _register(client)
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=snake["id"])

    resp = client.get(f"/snakes/{snake['id']}/chat/traces")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["traces"]) == 1
    assert data["traces"][0]["trace_id"] == trace_id


def test_list_traces_only_returns_own_snake(client):
    import agent.routes.ai_snake_trace_store as _ts
    s1 = _register(client, "Snake1")
    s2 = _register(client, "Snake2")
    store = _ts.get_trace_store()
    store.new_trace(snake_id=s1["id"])
    store.new_trace(snake_id=s2["id"])

    resp1 = client.get(f"/snakes/{s1['id']}/chat/traces")
    resp2 = client.get(f"/snakes/{s2['id']}/chat/traces")

    assert len(resp1.get_json()["traces"]) == 1
    assert len(resp2.get_json()["traces"]) == 1


# ── Trace detail ──────────────────────────────────────────────────────────────


def test_trace_detail_returns_correct_trace(client):
    import agent.routes.ai_snake_trace_store as _ts
    snake = _register(client)
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=snake["id"])
    store.complete_trace(trace_id, status="completed")

    resp = client.get(f"/snakes/{snake['id']}/chat/traces/{trace_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["trace"]["trace_id"] == trace_id
    assert data["trace"]["status"] == "completed"


def test_trace_detail_wrong_snake_returns_403(client):
    import agent.routes.ai_snake_trace_store as _ts
    s1 = _register(client, "OwnerSnake")
    s2 = _register(client, "OtherSnake")
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=s1["id"])

    resp = client.get(f"/snakes/{s2['id']}/chat/traces/{trace_id}")
    assert resp.status_code == 403


# ── Trace events ──────────────────────────────────────────────────────────────


def test_trace_events_returns_all_events(client):
    import agent.routes.ai_snake_trace_store as _ts
    from agent.routes.ai_snake_trace_store import TraceRecorder
    snake = _register(client)
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=snake["id"])
    rec = TraceRecorder(store, trace_id)
    rec.event("request_received", "Start")
    rec.event("llm_call_started", "LLM")
    rec.event("chat_message_written", "Fertig")

    resp = client.get(f"/snakes/{snake['id']}/chat/traces/{trace_id}/events?since_seq=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["events"]) == 3
    assert data["trace_id"] == trace_id
    assert data["current_status"] == "running"


def test_trace_events_since_seq_incremental(client):
    import agent.routes.ai_snake_trace_store as _ts
    from agent.routes.ai_snake_trace_store import TraceRecorder
    snake = _register(client)
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=snake["id"])
    rec = TraceRecorder(store, trace_id)
    rec.event("request_received", "Start")
    rec.event("codecompass_retrieval_started", "Retrieval")

    resp = client.get(f"/snakes/{snake['id']}/chat/traces/{trace_id}/events?since_seq=1")
    data = resp.get_json()
    assert len(data["events"]) == 1
    assert data["events"][0]["phase"] == "codecompass_retrieval_started"


def test_trace_events_completed_trace(client):
    import agent.routes.ai_snake_trace_store as _ts
    from agent.routes.ai_snake_trace_store import TraceRecorder
    snake = _register(client)
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=snake["id"])
    rec = TraceRecorder(store, trace_id)
    rec.event("request_received", "Start")
    store.complete_trace(trace_id)

    resp = client.get(f"/snakes/{snake['id']}/chat/traces/{trace_id}/events?since_seq=0")
    data = resp.get_json()
    assert data["current_status"] == "completed"


def test_trace_events_wrong_snake_returns_403(client):
    import agent.routes.ai_snake_trace_store as _ts
    s1 = _register(client, "Owner")
    s2 = _register(client, "Other")
    store = _ts.get_trace_store()
    trace_id = store.new_trace(snake_id=s1["id"])

    resp = client.get(f"/snakes/{s2['id']}/chat/traces/{trace_id}/events")
    assert resp.status_code == 403
