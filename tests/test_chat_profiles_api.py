from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from agent.routes.chat import chat_bp
from client_surfaces.operator_tui.chat_state import default_sessions, make_session


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    return app.test_client()


def _store(sessions: list[dict] | None = None):
    data = {
        "chat_sessions": copy.deepcopy(sessions or []),
        "chat_active_session_id": (sessions or [{}])[0].get("id", "") if sessions else "",
        "chat_folders": [],
        "chat_profiles": [],
    }
    manager = MagicMock()
    manager.load.side_effect = lambda: copy.deepcopy(data)

    def save(patch_data: dict) -> bool:
        data.update(copy.deepcopy(patch_data))
        return True

    manager.save.side_effect = save
    return data, patch("agent.routes.chat.get_manager", return_value=manager)


def test_builtin_profiles_are_configs_not_default_conversations(client):
    _, manager_patch = _store()
    with manager_patch:
        profiles = client.get("/api/chat/profiles")
        chats = client.get("/api/chat/sessions")

    assert {profile["id"] for profile in profiles.json} >= {"general", "code-help", "arch-overview"}
    assert all(profile["builtin"] for profile in profiles.json)
    assert [chat["id"] for chat in chats.json] == ["chat-default", "ananta-visual"]


def test_legacy_profile_session_is_migrated_without_changing_identity(client):
    legacy = next(session for session in default_sessions() if session["id"] == "code-help")
    legacy.pop("profile_id", None)
    legacy.pop("system_prompt_override", None)
    _, manager_patch = _store([legacy])
    with manager_patch:
        response = client.get("/api/chat/sessions")

    migrated = response.json[0]
    assert migrated["id"] == "code-help"
    assert migrated["profile_id"] == "code-help"
    assert migrated["system_prompt_override"] == ""


def test_custom_profile_can_be_assigned_and_cannot_be_deleted_while_used(client):
    _, manager_patch = _store([make_session(session_id="chat-1", name="Chat")])
    with manager_patch:
        created = client.post(
            "/api/chat/profiles",
            json={
                "id": "security-review",
                "name": "Security Review",
                "system_prompt": "Review security boundaries.",
                "settings": {"chat_backend": "lmstudio"},
            },
        )
        assigned = client.patch("/api/chat/sessions/chat-1", json={"profile_id": "security-review"})
        refreshed = client.get("/api/chat/sessions/chat-1")
        deleted = client.delete("/api/chat/profiles/security-review")

    assert created.status_code == 201
    assert assigned.json["profile_id"] == "security-review"
    assert assigned.json["system_prompt"] == "Review security boundaries."
    assert assigned.json["settings"]["chat_backend"] == "lmstudio"
    assert refreshed.json["settings"]["chat_backend"] == "lmstudio"
    assert deleted.status_code == 409
