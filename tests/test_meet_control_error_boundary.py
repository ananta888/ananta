"""Terminal upstream failures cannot turn into temporary Hub read outages."""

import errno
import io
import ssl
import time
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import request_signature
from tests.test_meet_observation_transport import transport
from worker.meet_media.contract import encode


@pytest.mark.parametrize(
    "error,temporary",
    [
        (HTTPError("https://private.invalid", status, "private", {}, io.BytesIO(b"private")), status in {502, 503, 504})
        for status in (401, 403, 409, 429, 500, 502, 503, 504)
    ]
    + [
        (URLError(ssl.SSLCertVerificationError("private certificate")), False),
        (ValueError("private parser"), False),
        (OSError("private unknown"), False),
        (URLError(ConnectionRefusedError(errno.ECONNREFUSED, "private host")), True),
    ],
)
def test_backchannel_only_marks_explicit_transient_transport_failures_unavailable(monkeypatch, error, temporary):
    client, _, opener, _, _, args = transport(monkeypatch)
    opener.open.side_effect = error
    with pytest.raises(MeetError) as caught:
        client.observe("task", "dispatch", "runtime", args[2])
    assert (caught.value.code, caught.value.status) == (
        ("meet_authorization_unavailable", 503) if temporary else ("meet_authorization_failed", 502)
    )
    assert "private" not in str(caught.value) and opener.open.call_count == 1
    if isinstance(error, HTTPError):
        assert error.closed


@pytest.mark.parametrize("action", ["exchange", "finish"])
@pytest.mark.parametrize(
    "code,status",
    [
        ("meet_authorization_unavailable", 503),
        ("meet_authorization_contract_invalid", 502),
        ("meet_authorization_failed", 502),
        ("meet_dialog_storage_unavailable", 503),
        ("meet_dialog_policy_denied", 403),
    ],
)
def test_callback_preserves_error_body_status_and_adds_only_restrictive_header(action, code, status):
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    key, service = b"synthetic-test-key" * 2, Mock()
    app.extensions.update(meet_binding_service=Mock(), meet_dialog_service=service, meet_media_worker_key=key)
    getattr(service, action).side_effect = MeetError(code, status)
    payload = {
        "schema": "ananta.meet-dialog-callback.v1",
        "action": action,
        "task_id": "task",
        "lease_id": "dispatch",
        "runtime_id": "runtime",
        "nonce": "a" * 32,
        "sent_at": int(time.time()),
    }
    payload.update({"meet_session_id": "ms_" + "a" * 32} if action == "exchange" else {"status": "failed"})
    body = encode(payload)
    response = app.test_client().post(
        "/api/meet/v1/internal/dialog",
        data=body,
        headers={"Content-Type": "application/json", "X-Ananta-Dialog-Signature": request_signature(key, body)},
    )
    assert response.status_code == status and response.json == {"error": {"code": code}}
    temporary = action == "exchange" and code == "meet_authorization_unavailable" and status == 503
    assert response.headers.get("X-Ananta-Dialog-Terminal") == (None if temporary else "1")
    assert response.headers["Cache-Control"] == "no-store"
