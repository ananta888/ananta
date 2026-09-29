"""Headless replay/downgrade tests with synthetic keys and actual HTTP callbacks."""

import io
import json
import threading
import time
from unittest.mock import Mock

import pytest
from flask import Flask
from werkzeug.serving import make_server

from agent.routes.meet import meet_bp
from ananta_contracts.meet_lease import lease_response_signature, validate_lease_request
from worker.meet_media.contract import encode, signature
from worker.meet_media.lease_guard import HubLeaseGuard

pytestmark = pytest.mark.timeout(15)
KEY = b"synthetic-lease-test-key-000000000"
PATH = "/api/meet/v1/internal/lease"


@pytest.fixture
def lease_app(monkeypatch, tmp_path):
    key = tmp_path / "key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key))
    monkeypatch.setenv("MEET_HUB_LEASE_URL", "http://hub.test" + PATH)
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions["meet_binding_service"] = Mock()
    app.extensions["meet_turn_service"] = Mock()
    app.extensions["meet_turn_service"].lease_allowed.return_value = True
    app.extensions["meet_media_worker_key"] = KEY
    return app


def request_body(**changes):
    return encode({"task_id": "task", "lease_id": "lease", "nonce": "a" * 32} | changes)


def test_route_binds_response_to_exact_request_and_rejects_legacy_protocol(lease_app):
    client = lease_app.test_client()
    body = request_body()
    response = client.post(PATH, data=body, headers={"X-Ananta-Task-Signature": signature(KEY, body)})
    assert response.status_code == 200 and response.json == {"allowed": True}
    assert response.headers["X-Ananta-Lease-Protocol"] == "ananta.meet-lease.v2"
    assert response.headers["X-Ananta-Lease-Signature"] == lease_response_signature(KEY, body, response.data)
    for changed in (request_body(nonce="b" * 32), request_body(task_id="other"), request_body(lease_id="other")):
        assert response.headers["X-Ananta-Lease-Signature"] != lease_response_signature(KEY, changed, response.data)
    legacy = encode({"task_id": "task", "lease_id": "lease"})
    response = client.post(PATH, data=legacy, headers={"X-Ananta-Task-Signature": signature(KEY, legacy)})
    assert response.status_code == 409 and response.json["error"]["code"] == "meet_lease_protocol_upgrade_required"
    lease_app.extensions["meet_turn_service"].lease_allowed.assert_called_once_with("task", "lease")


@pytest.mark.parametrize(
    "change", [{"nonce": ""}, {"nonce": True}, {"nonce": "A" * 32}, {"extra": True}, {"task_id": []}]
)
def test_nonce_and_scope_are_closed_and_cannot_reach_authority(lease_app, change):
    body = request_body(**change)
    with pytest.raises(ValueError):
        validate_lease_request(json.loads(body))
    response = lease_app.test_client().post(PATH, data=body, headers={"X-Ananta-Task-Signature": signature(KEY, body)})
    assert response.status_code == 401
    lease_app.extensions["meet_turn_service"].lease_allowed.assert_not_called()


class Reply(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers


def test_recorded_allowed_response_cannot_be_reused_for_next_poll_or_another_lease(lease_app, monkeypatch):
    opener = Mock()
    requests, signed = [], []

    def respond(request, **_kwargs):
        requests.append(json.loads(request.data))
        body = b'{"allowed":true}'
        if not signed:
            signed.append(lease_response_signature(KEY, request.data, body))
        return Reply(body, {"X-Ananta-Lease-Protocol": "ananta.meet-lease.v2", "X-Ananta-Lease-Signature": signed[0]})

    opener.open.side_effect = respond
    monkeypatch.setattr("worker.meet_media.lease_guard.urllib.request.build_opener", lambda *_: opener)
    guard = HubLeaseGuard("task", "lease")
    guard.require()
    with pytest.raises(ValueError, match="revoked_or_unavailable"):
        guard.require()
    with pytest.raises(ValueError, match="revoked_or_unavailable"):
        HubLeaseGuard("other-task", "other-lease").require()
    assert len({body["nonce"] for body in requests}) == 3


def test_worker_rejects_validly_signed_v1_downgrade(lease_app, monkeypatch):
    opener = Mock()
    body = b'{"allowed":true}'
    opener.open.side_effect = lambda *_args, **_kwargs: Reply(
        body,
        {
            "X-Ananta-Lease-Signature": signature(KEY, b"lease-v1\0" + body),
        },
    )
    monkeypatch.setattr("worker.meet_media.lease_guard.urllib.request.build_opener", lambda *_: opener)
    with pytest.raises(ValueError, match="revoked_or_unavailable"):
        HubLeaseGuard("task", "lease").require()


def test_expired_turn_cannot_poll_or_refresh_itself(lease_app, monkeypatch):
    opener = Mock()
    monkeypatch.setattr("worker.meet_media.lease_guard.urllib.request.build_opener", opener)
    with pytest.raises(ValueError, match="revoked_or_unavailable"):
        HubLeaseGuard("task", "lease", deadline=time.time() - 1).require()
    opener.assert_not_called()


def test_real_http_callback_observes_revocation_without_user_interaction(lease_app, monkeypatch):
    server = make_server("127.0.0.1", 0, lease_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("MEET_HUB_LEASE_URL", f"http://127.0.0.1:{server.server_port}{PATH}")
        guard = HubLeaseGuard("task", "lease", deadline=time.time() + 10)
        guard.require()
        lease_app.extensions["meet_turn_service"].lease_allowed.return_value = False
        with pytest.raises(ValueError, match="revoked_or_unavailable"):
            guard.require()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
