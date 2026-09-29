from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from agent.routes.chat import chat_bp
from agent.services.user_session_tokens import issue_user_access_token
from client_surfaces.operator_tui.chat_state import default_sessions, make_session


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    client = app.test_client()
    token = issue_user_access_token(username="admin", role="admin")
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client


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


def _auth_headers(username: str) -> dict[str, str]:
    token = issue_user_access_token(username=username, role="admin")
    return {"Authorization": f"Bearer {token}"}


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
                "settings": {"chat_backend": "lmstudio", "chat_backend_model": "review-model"},
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
    assert refreshed.json["settings"]["chat_backend_model"] == "review-model"
    assert refreshed.json["profile_settings"]["chat_backend_model"] == "review-model"
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
    assert "openai" in backend["allowed_values"]

    api_base = next(item for item in response.json["settings"] if item["key"] == "chat_backend_api_base")
    assert "openai" in api_base["visible_when"]["chat_backend"]
    assert "https://api.openai.com/v1" in api_base["suggestions"]

    credential_ref = next(
        item for item in response.json["settings"] if item["key"] == "chat_backend_credential_ref"
    )
    assert "openai" not in credential_ref["visible_when"]["chat_backend"]


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
        client.post(
            "/api/chat/profiles", json={"id": "preview", "name": "Preview", "settings": {"chat_backend": "opencode"}}
        )
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
            json={
                "id": "secure",
                "name": "Secure",
                "settings": {"chat_backend_credential_ref": "env://LOCAL_PROVIDER_API_KEY"},
            },
        )
        rejected = client.post(
            "/api/chat/profiles",
            json={"id": "unsafe", "name": "Unsafe", "settings": {"chat_backend_api_key": "plaintext"}},
        )

    assert accepted.status_code == 201
    assert accepted.json["settings"]["chat_backend_credential_ref"] == "env://LOCAL_PROVIDER_API_KEY"
    assert rejected.status_code == 422


def test_v3_migration_quarantines_unknown_profile_keys_idempotently(client):
    store, manager_patch = _store()
    store["chat_profiles"] = [
        {"id": "legacy", "name": "Legacy", "settings": {"chat_backend": "opencode", "retired_key": "keep-me"}}
    ]
    with manager_patch:
        first = client.get("/api/chat/profiles")
        snapshot = copy.deepcopy(store["chat_profiles"])
        second = client.get("/api/chat/profiles")
    profile = next(item for item in first.json if item["id"] == "legacy")
    assert profile["settings"] == {"chat_backend": "opencode"}
    assert profile["legacy_settings"] == {"retired_key": "keep-me"}
    assert store["chat_profiles"] == snapshot
    assert second.status_code == 200


def test_effective_preview_uses_global_profile_session_precedence(client):
    _, manager_patch = _store()
    with manager_patch:
        client.post(
            "/api/chat/profiles", json={"id": "layers", "name": "Layers", "settings": {"chat_max_tokens": 2000}}
        )
        response = client.post(
            "/api/chat/profiles/effective-preview",
            json={"profile_id": "layers", "session_settings_delta": {"chat_max_tokens": 3000}},
        )
    assert response.status_code == 200
    assert response.json["values"]["chat_max_tokens"] == {"value": 3000, "source": "session"}


def test_profile_validation_covers_ranges_urls_provider_scope_and_empty_model_inheritance(client):
    _, manager_patch = _store()
    with manager_patch:
        bad_range = client.post(
            "/api/chat/profiles", json={"id": "bad-range", "name": "Bad", "settings": {"chat_max_tokens": 999999}}
        )
        bad_url = client.post(
            "/api/chat/profiles",
            json={
                "id": "bad-url",
                "name": "Bad",
                "settings": {"chat_backend": "lmstudio", "chat_backend_api_base": "file:///tmp/model"},
            },
        )
        wrong_provider = client.post(
            "/api/chat/profiles",
            json={
                "id": "wrong-provider",
                "name": "Bad",
                "settings": {"chat_backend": "ananta-worker", "chat_backend_api_base": "http://localhost:1234/v1"},
            },
        )
        inherited_model = client.post(
            "/api/chat/profiles", json={"id": "inherit-model", "name": "Good", "settings": {"chat_backend_model": ""}}
        )
    assert bad_range.status_code == 422 and bad_range.json["issues"][0]["error_code"] == "out_of_range"
    assert bad_url.status_code == 422 and bad_url.json["issues"][0]["error_code"] == "invalid_url"
    assert (
        wrong_provider.status_code == 422
        and wrong_provider.json["issues"][0]["error_code"] == "setting_not_allowed_for_provider"
    )
    assert inherited_model.status_code == 201 and "chat_backend_model" not in inherited_model.json["settings"]


def test_v3_session_migration_preserves_unknown_delta_in_quarantine(client):
    legacy = make_session(session_id="legacy-v3", name="Legacy")
    legacy["settings_delta"] = {"chat_backend": "opencode", "retired_session_key": 7}
    legacy.pop("process_ref", None)
    store, manager_patch = _store([legacy])
    with manager_patch:
        response = client.get("/api/chat/sessions")
    migrated = response.json[0]
    assert migrated["settings_delta"] == {"chat_backend": "opencode"}
    assert migrated["legacy_settings_delta"] == {"retired_session_key": 7}
    assert migrated["process_ref"] is None and migrated["process_runs"] == []
    assert store["chat_model_version"] == 3


def test_profile_routes_require_authentication():
    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    anonymous = app.test_client()
    assert anonymous.get("/api/chat/profiles").status_code == 401
    assert anonymous.post("/api/chat/profiles", json={"id": "p", "name": "P"}).status_code == 401
    assert anonymous.patch("/api/chat/profiles/p", json={"name": "X"}).status_code == 401


def test_foreign_custom_profile_is_hidden_and_immutable(client):
    data, manager_patch = _store()
    data["chat_profiles"] = [
        {
            "id": "foreign-profile",
            "name": "Foreign",
            "settings": {},
            "process_ref": None,
            "owner_principal": {"tenant_id": "owner", "subject_id": "owner"},
        }
    ]
    headers = _auth_headers("intruder")
    with manager_patch:
        listed = client.get("/api/chat/profiles", headers=headers)
        updated = client.patch(
            "/api/chat/profiles/foreign-profile",
            json={"process_ref": {"graph_id": "attacker-graph"}},
            headers=headers,
        )
        effective = client.get("/api/chat/profiles/foreign-profile/effective", headers=headers)
        deleted = client.delete("/api/chat/profiles/foreign-profile", headers=headers)
    assert "foreign-profile" not in {profile["id"] for profile in listed.json}
    assert updated.status_code == effective.status_code == deleted.status_code == 404
    assert data["chat_profiles"][0]["name"] == "Foreign"


def test_profile_owner_metadata_is_not_exposed(client):
    data, manager_patch = _store()
    data["chat_profiles"] = [
        {
            "id": "owned-profile",
            "name": "Owned",
            "settings": {},
            "process_ref": None,
            "owner_principal": {"tenant_id": "admin", "subject_id": "admin"},
        }
    ]
    with manager_patch:
        listed = client.get("/api/chat/profiles")
    owned = next(profile for profile in listed.json if profile["id"] == "owned-profile")
    assert "owner_principal" not in owned
