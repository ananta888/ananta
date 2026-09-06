"""Headless API boundary tests with explicit synthetic authentication/service ports."""

import base64
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes.persona_media import persona_media_bp

pytestmark = pytest.mark.timeout(20)


@pytest.fixture
def client(monkeypatch):
    import agent.auth as auth

    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(persona_media_bp)
    app.extensions["persona_assets"] = Mock()
    app.extensions["persona_image_policy"] = Mock()
    monkeypatch.setattr(
        auth,
        "_validate_user_jwt",
        lambda token: {"sub": "actor", "tenant_id": "tenant", "project_id": "project", "role": "user"}
        if token == "synthetic-user"
        else None,
    )
    monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda _: True)
    return app.test_client(), app


HEADERS = {"Authorization": "Bearer synthetic-user"}
BASE = "/api/persona-media/v1/projects/project/images"


def payload():
    return {
        "content": base64.b64encode(b"synthetic-image-input").decode(),
        "media_type": "image/png",
        "origin_binding": "synthetic-origin",
        "license_binding": "synthetic-license",
        "consent_binding": None,
    }


@pytest.mark.parametrize("token", [None, "worker-token", "service-token", "user  extra"])
def test_user_asset_surface_never_uses_machine_credentials(client, token):
    http, app = client
    response = http.post(BASE, json=payload(), headers={"Authorization": f"Bearer {token}"} if token else {})
    assert response.status_code == 401
    app.extensions["persona_assets"].admit_image.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"content": "bad-base64"},
        {"content": []},
        {"origin_binding": {}},
        {"media_type": "image/svg+xml"},
        {"consent_binding": "https://elsewhere/consent"},
    ],
)
def test_closed_upload_rejects_metadata_and_content_confusion(client, change):
    http, app = client
    assert http.post(BASE, json=payload() | change, headers=HEADERS).status_code == 409
    app.extensions["persona_assets"].admit_image.assert_not_called()


def test_preview_never_requests_publication_and_sets_private_response_headers(client):
    http, app = client
    service = app.extensions["persona_assets"]
    service.read_image.return_value = b"synthetic-normalized-png"
    response = http.get(BASE + "/image/preview", headers=HEADERS)
    assert response.status_code == 200 and response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert service.read_image.call_args.kwargs == {"purpose": "preview"}
    assert http.get(BASE + "/image/publish", headers=HEADERS).status_code == 404
    assert http.get(BASE + "/image/preview?purpose=publish", headers=HEADERS).status_code == 400


def test_upload_passes_authenticated_scope_and_revocation_uses_strict_revision(client):
    http, app = client
    service = app.extensions["persona_assets"]
    service.admit_image.return_value.model_dump.return_value = {"classification": "test_only"}
    response = http.post(BASE, json=payload(), headers=HEADERS)
    assert response.status_code == 201 and response.json["revision"] == 2
    args, kwargs = service.admit_image.call_args
    assert (args[0].tenant_id, args[0].subject_id, args[1]) == ("tenant", "actor", "project")
    assert kwargs["content"] == b"synthetic-image-input"
    service.revoke.return_value = 3
    assert http.delete(BASE + "/image", json={"expected_revision": 2}, headers=HEADERS).json["revision"] == 3
    for revision in (True, 0, "2", 2**53):
        assert http.delete(BASE + "/image", json={"expected_revision": revision}, headers=HEADERS).status_code == 409


def test_user_bearer_cannot_satisfy_the_read_only_worker_callback(client):
    http, app = client
    app.extensions["persona_image_leases"] = Mock()
    app.extensions["persona_image_worker_key"] = b"synthetic-key-00000000000000000000"
    response = http.post("/api/persona-media/v1/internal/image-lease", json={"allowed": True}, headers=HEADERS)
    assert response.status_code == 403
    app.extensions["persona_image_leases"].require.assert_not_called()


def test_disabled_and_non_hub_instances_do_not_execute(client):
    http, app = client
    app.config["ROLE"] = "worker"
    assert http.post(BASE, json=payload(), headers=HEADERS).status_code == 403
    app.config["ROLE"] = "hub"
    app.extensions.pop("persona_assets")
    assert http.post(BASE, json=payload(), headers=HEADERS).status_code == 409
