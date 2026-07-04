"""API-Tests für Chat-Ordner (GET/POST /api/chat/folders, PATCH/DELETE /api/chat/folders/<id>)."""
from __future__ import annotations

import copy
import re
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

FOLDER_ID_RE = re.compile(r"^folder-[0-9a-f]{12}$")


def _make_session(session_id: str, **kwargs) -> dict:
    """Fully populated session dict (same shape the routes persist)."""
    from client_surfaces.operator_tui.chat_state import make_session

    return make_session(session_id=session_id, name=kwargs.pop("name", session_id), **kwargs)


@contextmanager
def store_ctx(sessions: list | None = None, folders: list | None = None, active_id: str = ""):
    """Patch get_manager in chat routes so _load_chat/_save_chat and
    _load_folders/_save_folders operate on a controlled in-memory store
    instead of the real user.json file. The store persists across requests
    made within the same context, so persistence behaviour is testable."""
    sessions = sessions or []
    store: dict = {
        "chat_sessions": copy.deepcopy(sessions),
        "chat_active_session_id": active_id or (sessions[0]["id"] if sessions else ""),
        "chat_folders": copy.deepcopy(folders or []),
    }

    mock_mgr = MagicMock()
    mock_mgr.load.side_effect = lambda: copy.deepcopy(store)

    def _save(data: dict) -> bool:
        # Real manager merges the given keys into user.json
        for key, value in data.items():
            store[key] = value
        return True

    mock_mgr.save.side_effect = _save

    with patch("agent.routes.chat.get_manager", return_value=mock_mgr):
        yield store, mock_mgr


# ── GET /api/chat/folders ─────────────────────────────────────────────────

def test_list_folders_initially_empty(client):
    with store_ctx():
        r = client.get("/api/chat/folders")
    assert r.status_code == 200
    assert r.json == []


# ── POST /api/chat/folders ────────────────────────────────────────────────

def test_create_folder_success(client):
    with store_ctx() as (store, _):
        r = client.post("/api/chat/folders", json={"name": "Projekte"})
    assert r.status_code == 201
    folder = r.json
    assert FOLDER_ID_RE.match(folder["id"]), folder["id"]
    assert folder["name"] == "Projekte"
    assert folder["icon"] == "📁"  # default icon
    assert folder["parent_id"] == ""
    assert isinstance(folder["created_at"], float)
    assert isinstance(folder["updated_at"], float)
    # Persisted into the store
    assert any(f["id"] == folder["id"] for f in store["chat_folders"])


def test_create_folder_with_explicit_id_then_duplicate_conflict(client):
    with store_ctx():
        r1 = client.post("/api/chat/folders", json={"id": "folder-custom", "name": "Erster"})
        assert r1.status_code == 201
        assert r1.json["id"] == "folder-custom"
        r2 = client.post("/api/chat/folders", json={"id": "folder-custom", "name": "Zweiter"})
    assert r2.status_code == 409
    assert "already exists" in r2.json["error"]


def test_create_folder_without_name_returns_400(client):
    with store_ctx():
        r = client.post("/api/chat/folders", json={"icon": "📁"})
        assert r.status_code == 400
        assert "name" in r.json["error"]
        # Whitespace-only name counts as missing too
        r2 = client.post("/api/chat/folders", json={"name": "   "})
    assert r2.status_code == 400


# ── PATCH /api/chat/folders/<id> ──────────────────────────────────────────

def test_patch_folder_updates_fields_and_timestamp(client):
    with store_ctx():
        created = client.post("/api/chat/folders", json={"name": "Alt"}).json
        r = client.patch(f"/api/chat/folders/{created['id']}", json={
            "name": "Neu", "icon": "🗂️", "parent_id": "folder-parent123",
        })
    assert r.status_code == 200
    assert r.json["name"] == "Neu"
    assert r.json["icon"] == "🗂️"
    assert r.json["parent_id"] == "folder-parent123"
    assert r.json["updated_at"] > created["updated_at"]
    assert r.json["created_at"] == created["created_at"]


def test_patch_unknown_folder_returns_404(client):
    with store_ctx():
        r = client.patch("/api/chat/folders/folder-nonexistent", json={"name": "X"})
    assert r.status_code == 404
    assert "not found" in r.json["error"]


# ── DELETE /api/chat/folders/<id> ─────────────────────────────────────────

def test_delete_folder_removes_it_from_list(client):
    with store_ctx():
        fid = client.post("/api/chat/folders", json={"name": "Weg damit"}).json["id"]
        r = client.delete(f"/api/chat/folders/{fid}")
        assert r.status_code == 204
        assert r.data == b""  # 204 must have no body
        listing = client.get("/api/chat/folders")
    assert not any(f["id"] == fid for f in listing.json)


def test_delete_unknown_folder_returns_404(client):
    with store_ctx():
        r = client.delete("/api/chat/folders/folder-nonexistent")
    assert r.status_code == 404
    assert "not found" in r.json["error"]


def test_delete_folder_moves_its_sessions_to_root(client):
    with store_ctx(sessions=[_make_session("keep-me")]):
        fid = client.post("/api/chat/folders", json={"name": "Projekt-X"}).json["id"]
        r = client.post("/api/chat/sessions", json={
            "id": "in-folder", "name": "In Folder", "folder_id": fid,
        })
        assert r.status_code == 201
        assert r.json["folder_id"] == fid

        assert client.delete(f"/api/chat/folders/{fid}").status_code == 204

        session = client.get("/api/chat/sessions/in-folder")
    assert session.status_code == 200
    assert session.json["folder_id"] == ""  # moved back to root


def test_folders_persist_across_requests(client):
    with store_ctx():
        created = client.post("/api/chat/folders", json={"name": "Dauerhaft", "icon": "💾"}).json
        # Fresh request against the same persisted store
        r = client.get("/api/chat/folders")
    assert r.status_code == 200
    assert len(r.json) == 1
    assert r.json[0]["id"] == created["id"]
    assert r.json[0]["name"] == "Dauerhaft"
    assert r.json[0]["icon"] == "💾"


def test_nested_folders_child_references_parent(client):
    with store_ctx():
        parent = client.post("/api/chat/folders", json={"name": "Eltern"}).json
        child = client.post("/api/chat/folders", json={
            "name": "Kind", "parent_id": parent["id"],
        }).json
        listing = client.get("/api/chat/folders").json
    ids = {f["id"] for f in listing}
    assert parent["id"] in ids and child["id"] in ids
    child_listed = next(f for f in listing if f["id"] == child["id"])
    assert child_listed["parent_id"] == parent["id"]
    parent_listed = next(f for f in listing if f["id"] == parent["id"])
    assert parent_listed["parent_id"] == ""
