import json
import time

import jwt
import pytest
from flask import Flask, g

from agent.auth import admin_required, check_auth, check_user_auth
from agent.config import settings


@pytest.fixture
def app():
    app = Flask(__name__)
    # AGENT_TOKEN muss mindestens 32 Bytes für JWT-HMAC-Validierung haben
    app.config["AGENT_TOKEN"] = "test-agent-token-that-is-at-least-32-bytes!"
    app.config["TESTING"] = True

    # Sicherstellen, dass secret_key für Tests gesetzt ist
    if not settings.secret_key:
        settings.secret_key = "test-secret-key"

    # Test routes
    @app.route("/secure")
    @check_auth
    def secure():
        return {"status": "ok", "is_admin": g.get("is_admin", False)}

    @app.route("/user-only")
    @check_user_auth
    def user_only():
        return {"status": "ok", "user": g.get("user")}

    @app.route("/admin-only")
    @admin_required
    def admin_only():
        return {"status": "ok", "is_admin": g.get("is_admin", False)}

    @app.route("/multi-auth")
    @check_auth
    @admin_required
    def multi_auth():
        return {"status": "ok", "is_admin": g.get("is_admin", False)}

    @app.route("/api/events/stream")
    @check_auth
    def control_center_stream():
        return {"status": "ok"}

    return app


@pytest.fixture
def client(app):
    return app.test_client()


# Token konstant für Tests (32+ Bytes für JWT-Validierung)
_AGENT_TOKEN = "test-agent-token-that-is-at-least-32-bytes!"


def test_agent_token_header(client):
    # Test static agent token in header
    headers = {"Authorization": f"Bearer {_AGENT_TOKEN}"}
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True


def test_agent_token_query(client):
    # Test static agent token in query param
    response = client.get(f"/secure?token={_AGENT_TOKEN}")
    assert response.status_code == 200
    assert response.json["is_admin"] is True


def test_agent_jwt_header(client, app):
    # Test JWT signed with AGENT_TOKEN
    token = jwt.encode({"sub": "hub", "exp": time.time() + 3600}, _AGENT_TOKEN, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True


def test_user_jwt_admin(client):
    # Test User JWT with admin role
    payload = {"username": "admin_user", "role": "admin", "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}

    # Works for @check_auth
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True

    # Works for @admin_required
    response = client.get("/admin-only", headers=headers)
    assert response.status_code == 200


def test_user_jwt_regular(client):
    # Test User JWT with user role
    payload = {"username": "regular_user", "role": "user", "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}

    # Works for @check_auth
    response = client.get("/secure", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is False

    # Works for @check_user_auth
    response = client.get("/user-only", headers=headers)
    assert response.status_code == 200

    # Fails for @admin_required
    response = client.get("/admin-only", headers=headers)
    assert response.status_code == 403


def test_invalid_token(client):
    headers = {"Authorization": "Bearer invalid-token"}
    response = client.get("/secure", headers=headers)
    assert response.status_code == 401


def test_control_center_stream_token_is_bound_to_its_get_route(client):
    token = jwt.encode(
        {
            "sub": "stream-user",
            "tenant_id": "stream-tenant",
            "role": "admin",
            "token_use": "control_center_stream",
            "cc_stream": True,
            "stream_user_id": "stream-user",
            "stream_tenant_id": "stream-tenant",
            "exp": time.time() + 60,
        },
        settings.secret_key,
        algorithm="HS256",
    )

    response = client.get("/secure", headers={"Authorization": f"Bearer {token}"})
    stream_response = client.get(
        "/api/events/stream",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == "user_token_scope_forbidden"
    assert stream_response.status_code == 200


def test_file_managed_service_auth_allows_only_bound_stream_user_query_token(
    client,
    app,
    tmp_path,
):
    service_secret = "file-managed-agent-token-that-is-at-least-32-bytes"
    token_file = tmp_path / "agent-token"
    token_file.write_text(service_secret, encoding="utf-8")
    token_file.chmod(0o600)
    app.config["AGENT_TOKEN"] = None
    app.config["AGENT_TOKEN_FILE"] = str(token_file)

    stream_token = jwt.encode(
        {
            "sub": "stream-user",
            "tenant_id": "stream-tenant",
            "role": "user",
            "token_use": "control_center_stream",
            "cc_stream": True,
            "stream_user_id": "stream-user",
            "stream_tenant_id": "stream-tenant",
            "exp": time.time() + 60,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    full_user_token = jwt.encode(
        {
            "sub": "stream-user",
            "tenant_id": "stream-tenant",
            "role": "user",
            "exp": time.time() + 60,
        },
        settings.secret_key,
        algorithm="HS256",
    )

    accepted = client.get(f"/api/events/stream?token={stream_token}")
    wrong_route = client.get(f"/secure?token={stream_token}")
    full_user_query = client.get(f"/secure?token={full_user_token}")
    service_query = client.get(f"/secure?token={service_secret}")

    assert accepted.status_code == 200
    assert wrong_route.status_code == 401
    assert full_user_query.status_code == 401
    assert service_query.status_code == 401


def test_stream_scope_is_enforced_when_agent_and_user_signing_secrets_match(
    client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "secret_key", _AGENT_TOKEN)
    token = jwt.encode(
        {
            "sub": "stream-user",
            "role": "admin",
            "token_use": "control_center_stream",
            "cc_stream": True,
            "exp": time.time() + 60,
        },
        _AGENT_TOKEN,
        algorithm="HS256",
    )

    response = client.get("/secure", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == "user_token_scope_forbidden"


def test_missing_token(client):
    response = client.get("/secure")
    assert response.status_code == 401


def test_invalid_initial_admin_identity_fails_before_user_lookup(monkeypatch):
    from agent import database

    class SessionMustNotOpen:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("database session opened before identity validation")

    monkeypatch.setattr(database.settings, "disable_initial_admin", False)
    monkeypatch.setattr(database.settings, "initial_admin_user", " admin ")
    monkeypatch.setattr(database, "Session", SessionMustNotOpen)

    with pytest.raises(
        RuntimeError,
        match="invalid_initial_admin_user:user_session_username_not_canonical",
    ):
        database.ensure_default_user()


def test_json_user_migration_validates_all_identities_before_saving(tmp_path):
    from agent.migrate_json_to_db import migrate_folder

    users_path = tmp_path / "users.json"
    users_path.write_text(
        json.dumps(
            {
                "valid-user": {"password": "hashed-password", "role": "user"},
                " invalid-user ": {"password": "hashed-password", "role": "user"},
            }
        ),
        encoding="utf-8",
    )

    class RecordingSession:
        def __init__(self):
            self.added = []

        def get(self, *_args, **_kwargs):
            return None

        def add(self, value):
            self.added.append(value)

    session = RecordingSession()

    with pytest.raises(
        ValueError,
        match="invalid_migrated_username:user_session_username_not_canonical",
    ):
        migrate_folder(str(tmp_path), session)

    assert session.added == []


def test_multi_decorator_auth(client):
    # Test combination of @check_auth and @admin_required
    payload = {"username": "admin_user", "role": "admin", "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/multi-auth", headers=headers)
    assert response.status_code == 200
    assert response.json["is_admin"] is True


def test_expired_token(client):
    payload = {"username": "user", "role": "user", "exp": time.time() - 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/user-only", headers=headers)
    assert response.status_code == 401
    assert "expired" in response.json["message"].lower()


def test_no_auth_configured(client, app):
    # If AGENT_TOKEN is not set, it should log a warning but allow (if that's the current behavior)
    app.config["AGENT_TOKEN"] = None
    response = client.get("/secure")
    assert response.status_code == 200
