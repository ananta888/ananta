"""API-Tests für Chat-Sitzungen."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agent.ai_agent import create_app
from agent.services.user_session_tokens import issue_user_access_token

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    return create_app(testing=True)


@pytest.fixture
def client(app):
    with app.test_client() as c:
        token = issue_user_access_token(username="admin", role="admin")
        c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        yield c


# ── Test helpers ──────────────────────────────────────────────────────────


def _default_session(session_id: str, name: str = "") -> dict:
    """Minimal session dict for tests — mirrors make_session output shape."""
    return {"id": session_id, "name": name or session_id, "settings": {}, "system_prompt": "", "icon": "💬"}


def _auth_headers(username: str) -> dict[str, str]:
    token = issue_user_access_token(username=username, role="admin")
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def sessions_ctx(sessions: list, active_id: str = ""):
    """Patch get_manager in chat routes so _load_chat / _save_chat operate on a
    controlled in-memory store instead of the real user.json file.

    Note: get_sessions() always ensures at least the default sessions when the
    stored list is empty (see chat_state.get_sessions for the guard). Tests that
    start with 0 sessions will therefore get the starter sessions back from
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
    """When none are persisted, return the starter chat and visual log."""
    with sessions_ctx([]):
        r = client.get("/api/chat/sessions")
    assert r.status_code == 200
    assert [session["id"] for session in r.json] == ["chat-default", "ananta-visual"]


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
        r = client.post(
            "/api/chat/sessions",
            json={
                "id": "new-session",
                "name": "Neue Session",
                "system_prompt": "Assistent.",
                "icon": "🌟",
                "settings": {"chat_backend": "ananta-worker"},
            },
        )
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
    assert r.json == {
        "error": "resource_id_unavailable",
        "error_code": "resource_id_unavailable",
    }


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
    session = {
        "id": "editable",
        "name": "Alt",
        "system_prompt": "old",
        "icon": "📝",
        "settings": {"chat_backend": "old"},
    }
    with sessions_ctx([session]) as (_, mock_mgr):
        r = client.put(
            "/api/chat/sessions/editable",
            json={
                "name": "Neu",
                "system_prompt": "new",
                "icon": "✨",
                "settings": {"chat_backend": "new-backend"},
            },
        )
    assert r.status_code == 200
    assert r.json["name"] == "Neu"
    assert r.json["system_prompt"] == "new"
    assert r.json["icon"] == "✨"
    assert r.json["settings"]["chat_backend"] == "new-backend"
    assert mock_mgr.save.call_count >= 1


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


def test_generic_session_routes_require_user_authentication(app):
    anonymous = app.test_client()
    assert anonymous.get("/api/chat/sessions").status_code == 401
    assert anonymous.post("/api/chat/sessions", json={"id": "x", "name": "X"}).status_code == 401
    assert anonymous.get("/api/chat/sessions/x").status_code == 401
    assert anonymous.patch("/api/chat/sessions/x", json={"name": "X"}).status_code == 401
    assert anonymous.delete("/api/chat/sessions/x").status_code == 401
    assert anonymous.post("/api/chat/sessions/x/activate").status_code == 401


def test_foreign_session_is_hidden_from_every_session_control_path(client):
    foreign = _default_session("private")
    foreign["owner_principal"] = {"tenant_id": "owner", "subject_id": "owner"}
    foreign["process_ref"] = {"graph_id": "foreign-graph", "version": "latest"}
    own = _default_session("intruder-own")
    own["owner_principal"] = {"tenant_id": "intruder", "subject_id": "intruder"}
    headers = _auth_headers("intruder")
    with sessions_ctx([foreign, own]):
        listed = client.get("/api/chat/sessions", headers=headers)
        fetched = client.get("/api/chat/sessions/private", headers=headers)
        updated = client.patch("/api/chat/sessions/private", json={"name": "stolen"}, headers=headers)
        activated = client.post("/api/chat/sessions/private/activate", headers=headers)
        process = client.get("/api/chat/sessions/private/process", headers=headers)
        context = client.get("/api/chat/sessions/private/context-overview", headers=headers)
        summary = client.post(
            "/api/chat/sessions/private/summarize",
            json={"messages": [{"text": "secret"}]},
            headers=headers,
        )
        preview = client.post(
            "/api/chat/sessions/private/prompt-preview",
            json={"message": "secret"},
            headers=headers,
        )
        deleted = client.delete("/api/chat/sessions/private", headers=headers)
    assert [item["id"] for item in listed.json] == ["intruder-own"]
    assert all(
        response.status_code == 404
        for response in (fetched, updated, activated, process, context, summary, preview, deleted)
    )


def test_cross_tenant_session_id_reservation_is_fail_closed(client):
    foreign = _default_session("reserved")
    foreign["owner_principal"] = {"tenant_id": "owner", "subject_id": "owner"}
    with sessions_ctx([foreign]):
        response = client.post(
            "/api/chat/sessions",
            json={"id": "reserved", "name": "Collision"},
            headers=_auth_headers("intruder"),
        )
    assert response.status_code == 409
    assert response.json == {
        "error": "resource_id_unavailable",
        "error_code": "resource_id_unavailable",
    }


def test_session_owner_metadata_is_never_returned(client):
    owned = _default_session("owned")
    owned["owner_principal"] = {"tenant_id": "admin", "subject_id": "admin"}
    with sessions_ctx([owned]):
        listed = client.get("/api/chat/sessions")
        fetched = client.get("/api/chat/sessions/owned")
    assert "owner_principal" not in listed.json[0]
    assert "owner_principal" not in fetched.json
