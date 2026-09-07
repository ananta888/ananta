"""Headless real Flask avatar boundaries with explicit synthetic authentication."""

import time
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
from ananta_contracts.meet_avatar_image import MAX_AVATAR_IMAGE_BYTES, image_request_signature, image_response_signature
from ananta_contracts.meet_dialog import request_signature
from tests.test_meet_avatar_image_contract import packet
from worker.meet_media.contract import encode

pytestmark = pytest.mark.timeout(30)


def application():
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    runtime = Mock()
    app.extensions.update(
        meet_binding_service=Mock(), meet_dialog_service=runtime, meet_media_worker_key=b"synthetic-avatar-key" * 2
    )
    return app, runtime


def test_actual_image_callback_checks_separate_signature_exact_body_and_no_store():
    app, runtime = application()
    request, result = packet()
    request["sent_at"] = int(time.time())
    request["binding"]["deadline_ms"] = (int(time.time()) + 60) * 1000
    result.update(binding=request["binding"])
    runtime.avatar_image.return_value = result
    key, client = app.extensions["meet_media_worker_key"], app.test_client()

    def post(raw, **headers):
        return client.post(
            "/api/meet/v1/internal/dialog/avatar-image",
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Ananta-Avatar-Signature": image_request_signature(key, raw),
                **headers,
            },
        )

    raw = encode(request)
    response = post(raw)
    assert response.status_code == 200
    assert response.headers["X-Ananta-Avatar-Signature"] == image_response_signature(key, raw, response.data)
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    runtime.avatar_image.assert_called_once_with(request)
    for invalid in (
        encode(request | {"url": "https://image"}),
        encode(request | {"sent_at": 1}),
        raw[:-1] + b',"nonce":"b"}',
    ):
        assert post(invalid).status_code == 400
    assert post(raw, Authorization="Bearer synthetic-user").status_code == 403
    assert post(raw, **{"X-Ananta-Avatar-Signature": request_signature(key, raw)}).status_code == 401
    assert post(b" " * 16385).status_code == 403
    runtime.avatar_image.assert_called_once()
    runtime.avatar_image.return_value = {"oversize": "x" * MAX_AVATAR_IMAGE_BYTES}
    assert post(raw).status_code == 502


def test_selection_route_is_user_owned_closed_and_requires_no_interactive_approval(monkeypatch):
    import agent.auth as auth

    app, runtime = application()
    monkeypatch.setattr(
        auth,
        "_validate_user_jwt",
        lambda token: {"sub": "actor", "tenant_id": "tenant", "project_id": "project", "role": "user"}
        if token == "synthetic-user"
        else None,
    )
    monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda _: True)
    runtime.select_avatar.return_value = {"selected": True}
    client, url = app.test_client(), "/api/meet/v1/projects/project/dialogs/task/avatar"
    headers = {"Authorization": "Bearer synthetic-user"}
    payload = {"expected_revision": 1, "neutral": True}
    assert client.put(url, json=payload).status_code == 401
    assert client.put(url, json=payload, headers=headers).status_code == 200
    principal, project, task, body = runtime.select_avatar.call_args.args
    assert (principal.subject_id, principal.tenant_id, project, task, body) == (
        "actor",
        "tenant",
        "project",
        "task",
        payload,
    )
    assert client.put(url + "?tenant=foreign", json=payload, headers=headers).status_code == 400
    assert client.put(url, data=b" " * 2049, headers=headers).status_code == 400
    assert client.put(url, data=b'{"neutral":true,"neutral":false}', headers=headers).status_code == 400
    runtime.select_avatar.assert_called_once()
