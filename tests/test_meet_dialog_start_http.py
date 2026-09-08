"""Optional header preserves legacy routes and uses current headless authentication."""

from unittest.mock import Mock

import pytest

from agent.services.meet_dialog_starts import MeetDialogStarts
from tests.test_meet_avatar_image_routes import application
from tests.test_meet_dialog_starts import PAYLOAD, RECEIPT
from tests.test_meet_dialog_starts import start_store as start_store

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def http(monkeypatch, store):
    import agent.auth as auth

    app, starter = application()
    starter.start.return_value = RECEIPT
    access = Mock()
    app.extensions["meet_dialog_starts"] = MeetDialogStarts(starter, store, access)
    monkeypatch.setattr(
        auth,
        "_validate_user_jwt",
        lambda token: (
            {"sub": "owner", "tenant_id": "tenant", "project_id": "project", "role": "user"}
            if token == "synthetic-user"
            else None
        ),
    )
    monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda _: True)
    return app.test_client(), app, starter, access


@pytest.mark.parametrize("suffix,parent", [("/dialogs", ""), ("/tasks/parent/dialogs", "parent")])
def test_keyed_starts_replay_same_body_and_mark_historical_receipt(http, suffix, parent):
    client, _, starter, access = http
    path = "/api/meet/v1/projects/project" + suffix
    headers = {"Authorization": "Bearer synthetic-user", "Idempotency-Key": "stable-request"}
    assert client.post(path, json=PAYLOAD, headers={"Idempotency-Key": "stable-request"}).status_code == 401
    first, replay = [client.post(path, json=PAYLOAD, headers=headers) for _ in range(2)]
    assert first.status_code == replay.status_code == 202
    assert first.json == replay.json == RECEIPT
    assert first.headers["Idempotency-Replayed"] == "false" and replay.headers["Idempotency-Replayed"] == "true"
    assert replay.headers["Cache-Control"] == "no-store" and replay.headers["Referrer-Policy"] == "no-referrer"
    assert starter.start.call_count == 1 and starter.start.call_args.args[3] == parent
    assert access.require_write_access.call_count == 2
    changed = client.post(path, json=PAYLOAD | {"duration_seconds": 600}, headers=headers)
    assert changed.status_code == 409 and changed.json == {"error": {"code": "meet_dialog_idempotency_conflict"}}


def test_legacy_start_is_unchanged_but_header_never_silently_falls_back(http):
    client, app, starter, _ = http
    path = "/api/meet/v1/projects/project/dialogs"
    headers = {"Authorization": "Bearer synthetic-user"}
    app.extensions.pop("meet_dialog_starts")
    legacy = client.post(path, json=PAYLOAD, headers=headers)
    assert legacy.status_code == 202 and "Idempotency-Replayed" not in legacy.headers
    denied = client.post(path, json=PAYLOAD, headers=headers | {"Idempotency-Key": "key"})
    assert denied.status_code == 503
    assert starter.start.call_count == 1


@pytest.mark.parametrize("key", ["", "comma,duplicate", "white space", "x" * 161])
def test_bad_header_cannot_start_a_session(http, key):
    client, _, starter, _ = http
    response = client.post(
        "/api/meet/v1/projects/project/dialogs",
        json=PAYLOAD,
        headers={"Authorization": "Bearer synthetic-user", "Idempotency-Key": key},
    )
    assert response.status_code == 400
    starter.start.assert_not_called()


def test_existing_cors_policy_allows_keyed_preflight_only_for_configured_frontend(http, monkeypatch):
    from agent.bootstrap.extensions import configure_cors, settings

    client, app, starter, _ = http
    monkeypatch.setattr(settings, "cors_origins", "https://synthetic-frontend.test")
    configure_cors(app)
    headers = {
        "Origin": "https://synthetic-frontend.test",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
    }
    path = "/api/meet/v1/projects/project/dialogs"
    allowed = client.options(path, headers=headers)
    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == headers["Origin"]
    assert "idempotency-key" in allowed.headers["Access-Control-Allow-Headers"].lower()
    denied = client.options(path, headers=headers | {"Origin": "https://synthetic-foreign.test"})
    assert "Access-Control-Allow-Origin" not in denied.headers
    starter.start.assert_not_called()
