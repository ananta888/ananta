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


def test_setting_schema_is_deterministic_and_scope_aware(client):
    response = client.get("/api/chat/settings/schema")

    assert response.status_code == 200
    assert response.json["schema_version"] == 1
    keys = [item["key"] for item in response.json["settings"]]
    assert keys == sorted(keys)
    backend = next(item for item in response.json["settings"] if item["key"] == "chat_backend")
    assert backend["scopes"] == ["global", "profile", "session"]
    assert "lmstudio" in backend["allowed_values"]


def test_profile_settings_reject_unknown_keys_and_support_null_reset(client):
    store, manager_patch = _store()
    with manager_patch:
        invalid = client.post(
            "/api/chat/profiles",
            json={"id": "invalid", "name": "Invalid", "settings": {"made_up_secret": "value"}},
        )
        created = client.post(
            "/api/chat/profiles",
            json={"id": "valid", "name": "Valid", "settings": {"chat_backend": "lmstudio"}},
        )
        reset = client.patch("/api/chat/profiles/valid", json={"settings": {"chat_backend": None}})

    assert invalid.status_code == 422
    assert invalid.json["issues"][0]["error_code"] == "unknown_setting"
    assert created.status_code == 201
    assert reset.status_code == 200
    assert "chat_backend" not in reset.json["settings"]
    assert store["chat_profiles"][0]["settings"] == {}


def test_effective_profile_preview_reports_value_provenance(client):
    _, manager_patch = _store()
    with manager_patch:
        client.post("/api/chat/profiles", json={"id": "preview", "name": "Preview", "settings": {"chat_backend": "opencode"}})
        preview = client.get("/api/chat/profiles/preview/effective")

    assert preview.status_code == 200
    assert preview.json["effective_settings"]["chat_backend"] == "opencode"
    assert preview.json["provenance"]["chat_backend"] == "profile"
    assert preview.json["provenance"]["chat_max_tokens"] == "global"


def test_profile_accepts_credential_reference_but_not_plain_secret(client):
    _, manager_patch = _store()
    with manager_patch:
        accepted = client.post(
            "/api/chat/profiles",
            json={"id": "secure", "name": "Secure", "settings": {"chat_backend_credential_ref": "secret://providers/local"}},
        )
        rejected = client.post(
            "/api/chat/profiles",
            json={"id": "unsafe", "name": "Unsafe", "settings": {"chat_backend_api_key": "plaintext"}},
        )

    assert accepted.status_code == 201
    assert accepted.json["settings"]["chat_backend_credential_ref"] == "secret://providers/local"
    assert rejected.status_code == 422
