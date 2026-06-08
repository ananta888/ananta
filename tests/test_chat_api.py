"""API-Tests für Chat-Sitzungen."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agent.ai_agent import create_app


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    return create_app(testing=True)


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# ── Test helpers ──────────────────────────────────────────────────────────

def _default_session(session_id: str, name: str = "") -> dict:
    """Minimal session dict for tests — mirrors make_session output shape."""
    return {"id": session_id, "name": name or session_id, "settings": {}, "system_prompt": "", "icon": "💬"}


@contextmanager
def sessions_ctx(sessions: list, active_id: str = ""):
    """Patch get_manager in chat routes so _load_chat / _save_chat operate on a
    controlled in-memory store instead of the real user.json file.

    Note: get_sessions() always ensures at least the default sessions when the
    stored list is empty (see chat_state.get_sessions for the guard). Tests that
    start with 0 sessions will therefore get the 3 default sessions back from
    GET /sessions — that is expected behaviour.
    """
    active_id = active_id or (sessions[0]["id"] if sessions else "")
    store: dict = {
        "chat_sessions": [s.copy() for s in sessions],
        "chat_active_session_id": active_id,
    }

    mock_mgr = MagicMock()
    mock_mgr.load.side_effect = lambda: {
        "chat_sessions": [s.copy() for s in store["chat_sessions"]],
        "chat_active_session_id": store["chat_active_session_id"],
    }

    def _save(data: dict) -> bool:
        if "chat_sessions" in data:
            store["chat_sessions"] = data["chat_sessions"]
        if "chat_active_session_id" in data:
            store["chat_active_session_id"] = data["chat_active_session_id"]
        return True

    mock_mgr.save.side_effect = _save

    with patch("agent.routes.chat.get_manager", return_value=mock_mgr):
        yield store, mock_mgr


# ── GET /api/chat/sessions ────────────────────────────────────────────────

def test_list_sessions_returns_defaults_when_none_stored(client):
    """When no sessions are persisted, the endpoint returns the 3 built-in default
    sessions — get_sessions() guarantees the list is never empty."""
    with sessions_ctx([]):
        r = client.get("/api/chat/sessions")
    assert r.status_code == 200
    assert len(r.json) == 3  # code-help, writing-coach, general


def test_list_sessions_with_saved_sessions(client):
    """When specific sessions are stored, only those are returned."""
    s1 = _default_session("code-help", "Code-Help")
    s2 = _default_session("writing-coach", "Schreib-Coach")
    with sessions_ctx([s1, s2]):
        r = client.get("/api/chat/sessions")
    assert r.status_code == 200
    assert len(r.json) == 2
    assert r.json[0]["id"] == "code-help"
    assert r.json[1]["id"] == "writing-coach"


# ── POST /api/chat/sessions ───────────────────────────────────────────────

def test_create_session_success(client):
    s1 = _default_session("existing")
    with sessions_ctx([s1]) as (store, mock_mgr):
        r = client.post("/api/chat/sessions", json={
            "id": "new-session", "name": "Neue Session",
            "system_prompt": "Assistent.", "icon": "🌟",
            "settings": {"chat_backend": "ananta-worker"},
        })
    assert r.status_code == 201
    assert r.json["id"] == "new-session"
    assert any(s["id"] == "new-session" for s in store["chat_sessions"])
    assert store["chat_active_session_id"] == "new-session"
    mock_mgr.save.assert_called_once()


def test_create_session_missing_id(client):
    with sessions_ctx([]):
        r = client.post("/api/chat/sessions", json={"name": "Unvollständig"})
    assert r.status_code == 400
    assert "Session ID and name are required" in r.json["error"]


def test_create_session_missing_name(client):
    with sessions_ctx([]):
        r = client.post("/api/chat/sessions", json={"id": "x"})
    assert r.status_code == 400


def test_create_session_duplicate_id(client):
    existing = _default_session("existing")
    with sessions_ctx([existing]):
        r = client.post("/api/chat/sessions", json={"id": "existing", "name": "Neu"})
    assert r.status_code == 409
    assert "already exists" in r.json["error"]


# ── GET /api/chat/sessions/<id> ───────────────────────────────────────────

def test_get_session_found(client):
    session = _default_session("specific", "Spezifisch")
    with sessions_ctx([session]):
        r = client.get("/api/chat/sessions/specific")
    assert r.status_code == 200
    assert r.json["id"] == "specific"


def test_get_session_not_found(client):
    s1 = _default_session("only")
    with sessions_ctx([s1]):
        r = client.get("/api/chat/sessions/non-existent")
    assert r.status_code == 404
    assert "error" in r.json


# ── PUT /api/chat/sessions/<id> ───────────────────────────────────────────

def test_update_session_success(client):
    session = {"id": "editable", "name": "Alt", "system_prompt": "old",
               "icon": "📝", "settings": {"chat_backend": "old"}}
    with sessions_ctx([session]) as (_, mock_mgr):
        r = client.put("/api/chat/sessions/editable", json={
            "name": "Neu", "system_prompt": "new", "icon": "✨",
            "settings": {"chat_backend": "new-backend"},
        })
    assert r.status_code == 200
    assert r.json["name"] == "Neu"
    assert r.json["system_prompt"] == "new"
    assert r.json["icon"] == "✨"
    assert r.json["settings"]["chat_backend"] == "new-backend"
    mock_mgr.save.assert_called_once()


def test_update_session_not_found(client):
    s1 = _default_session("only")
    with sessions_ctx([s1]):
        r = client.put("/api/chat/sessions/non-existent", json={"name": "Test"})
    assert r.status_code == 404


# ── DELETE /api/chat/sessions/<id> ───────────────────────────────────────

def test_delete_session_success(client):
    s1 = _default_session("removable")
    s2 = _default_session("keep")
    with sessions_ctx([s1, s2], active_id="removable") as (store, mock_mgr):
        r = client.delete("/api/chat/sessions/removable")
    assert r.status_code == 204
    assert r.data == b""  # 204 must have no body
    assert not any(s["id"] == "removable" for s in store["chat_sessions"])
    mock_mgr.save.assert_called_once()


def test_delete_session_not_found(client):
    s1 = _default_session("only")
    with sessions_ctx([s1]):
        r = client.delete("/api/chat/sessions/non-existent")
    assert r.status_code == 404


def test_delete_last_session_blocked(client):
    only = _default_session("only-one")
    with sessions_ctx([only]) as (store, mock_mgr):
        r = client.delete("/api/chat/sessions/only-one")
    assert r.status_code == 400
    assert "Cannot delete the last remaining session" in r.json["error"]
    assert len(store["chat_sessions"]) == 1
    mock_mgr.save.assert_not_called()


# ── POST /api/chat/sessions/<id>/activate ────────────────────────────────

def test_activate_session_success(client):
    s1 = _default_session("s1")
    s2 = _default_session("s2")
    with sessions_ctx([s1, s2], active_id="s1") as (store, mock_mgr):
        r = client.post("/api/chat/sessions/s2/activate")
    assert r.status_code == 200
    assert store["chat_active_session_id"] == "s2"
    mock_mgr.save.assert_called_once()


def test_activate_session_not_found(client):
    s1 = _default_session("only")
    with sessions_ctx([s1]):
        r = client.post("/api/chat/sessions/non-existent/activate")
    assert r.status_code == 404
