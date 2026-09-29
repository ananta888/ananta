from __future__ import annotations

import copy
from unittest.mock import patch

import pytest
from flask import Flask

from agent.routes.chat import chat_bp
from agent.services.user_session_tokens import issue_user_access_token
from client_surfaces.operator_tui.chat_state import make_session


class MemoryManager:
    def __init__(self) -> None:
        self.data = {
            "chat_sessions": [make_session(session_id="chat-1", name="Chat")],
            "chat_active_session_id": "chat-1",
            "chat_folders": [],
            "chat_profiles": [],
            "chat_session_types": [],
            "chat_organization_proposals": [],
            "chat_organization_revisions": [],
            "chat_model_version": 2,
        }

    def load(self) -> dict:
        return copy.deepcopy(self.data)

    def save(self, values: dict) -> bool:
        self.data.update(copy.deepcopy(values))
        return True


@pytest.fixture
def api():
    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    manager = MemoryManager()
    with patch("agent.routes.chat.get_manager", return_value=manager):
        client = app.test_client()
        token = issue_user_access_token(username="admin", role="admin")
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        yield client, manager


def test_folder_integrity_and_non_empty_delete(api) -> None:
    client, _ = api
    missing = client.post("/api/chat/folders", json={"name": "Child", "parent_id": "missing"})
    assert missing.status_code == 400
    assert missing.json["error_code"] == "folder_parent_invalid"

    parent = client.post("/api/chat/folders", json={"id": "parent", "name": "Parent"})
    child = client.post("/api/chat/folders", json={"id": "child", "name": "Child", "parent_id": "parent"})
    assert parent.status_code == child.status_code == 201
    cycle = client.patch("/api/chat/folders/parent", json={"parent_id": "child"})
    assert cycle.status_code == 400
    assert cycle.json["error_code"] == "folder_cycle"
    non_empty = client.delete("/api/chat/folders/parent")
    assert non_empty.status_code == 409
    assert non_empty.json["error_code"] == "folder_not_empty"


def test_session_references_are_validated(api) -> None:
    client, _ = api
    missing_folder = client.patch("/api/chat/sessions/chat-1", json={"folder_id": "missing"})
    assert missing_folder.status_code == 400
    assert missing_folder.json["error_code"] == "folder_not_found"

    bad_type = client.patch("/api/chat/sessions/chat-1", json={"session_type": "missing"})
    assert bad_type.status_code == 400
    assert bad_type.json["error_code"] == "type_not_found"

    created = client.post("/api/chat/types", json={"id": "work", "name": "Work", "subtypes": ["review"]})
    assert created.status_code == 201
    bad_subtype = client.patch("/api/chat/sessions/chat-1", json={"session_type": "work", "session_subtype": "write"})
    assert bad_subtype.status_code == 400
    assert bad_subtype.json["error_code"] == "subtype_not_allowed"
    assigned = client.patch("/api/chat/sessions/chat-1", json={"session_type": "work", "session_subtype": "review"})
    assert assigned.status_code == 200
    assert client.delete("/api/chat/types/work").status_code == 409


def test_persisted_proposal_apply_history_and_revert(api) -> None:
    client, manager = api
    snapshot = client.get("/api/chat/organization/snapshot")
    assert snapshot.status_code == 200
    assert len(snapshot.json["state_hash"]) == 64

    created = client.post(
        "/api/chat/organization/proposals",
        json={
            "summary": "Create work folder",
            "operations": [
                {
                    "operation_id": "create",
                    "type": "folder.create",
                    "temp_id": "work",
                    "after": {"name": "Work"},
                },
                {
                    "operation_id": "move",
                    "type": "conversation.move",
                    "target_id": "chat-1",
                    "after": "work",
                },
            ],
        },
    )
    proposal_id = created.json["id"]
    assert client.post(f"/api/chat/organization/proposals/{proposal_id}/validate").json["status"] == "ready"
    applied = client.post(f"/api/chat/organization/proposals/{proposal_id}/apply")
    assert applied.status_code == 200
    revision_id = applied.json["id"]
    assert manager.data["chat_sessions"][0]["folder_id"].startswith("folder-")
    assert client.post(f"/api/chat/organization/proposals/{proposal_id}/apply").json["id"] == revision_id
    assert len(client.get("/api/chat/organization/history").json) == 1

    reverted = client.post(f"/api/chat/organization/history/{revision_id}/revert")
    assert reverted.status_code == 200
    assert manager.data["chat_sessions"][0]["folder_id"] == ""
    assert len(client.get("/api/chat/organization/history").json) == 2


def test_global_chat_collections_require_exact_initial_admin(api) -> None:
    client, _ = api
    token = issue_user_access_token(username="other-admin", role="admin")
    headers = {"Authorization": f"Bearer {token}"}
    responses = (
        client.get("/api/chat/folders", headers=headers),
        client.post("/api/chat/folders", json={"name": "X"}, headers=headers),
        client.get("/api/chat/types", headers=headers),
        client.post("/api/chat/types", json={"id": "x", "name": "X"}, headers=headers),
        client.get("/api/chat/organization/snapshot", headers=headers),
        client.post("/api/chat/sessions/ai-reorganize", headers=headers),
    )
    assert all(response.status_code == 403 for response in responses)
    assert all(response.json["error_code"] == "global_chat_admin_required" for response in responses)
