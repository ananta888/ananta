"""Distinct headless HTTP envelope, no bearer/legacy-signature authorization."""

import time
from unittest.mock import Mock

from flask import Flask

from agent.routes.meet import meet_bp
from ananta_contracts.meet_dialog import request_signature
from ananta_contracts.meet_spoken_reply import MAX_SPOKEN_BYTES, spoken_request_signature, spoken_response_signature
from tests.test_meet_spoken_reply_contract import packet
from worker.meet_media.contract import encode


def test_separate_http_budget_hmac_domain_and_no_store_response():
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    key, service = b"synthetic-private-worker-key-32bytes", Mock()
    app.extensions.update(meet_binding_service=Mock(), meet_dialog_service=service, meet_media_worker_key=key)
    payload, _, result = packet(22050)
    payload["sent_at"] = int(time.time())
    service.spoken_reply.return_value = result
    body = encode(payload)
    client = app.test_client()
    path = "/api/meet/v1/internal/dialog/speech"

    def post(raw=body, **headers):
        return client.post(
            path,
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Ananta-Speech-Signature": spoken_request_signature(key, raw),
                **headers,
            },
        )

    response = post()
    assert response.status_code == 200 and 16384 < len(response.data) < MAX_SPOKEN_BYTES
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Ananta-Speech-Signature"] == spoken_response_signature(key, body, response.data)
    assert "X-Ananta-Dialog-Signature" not in response.headers
    assert post(**{"X-Ananta-Speech-Signature": request_signature(key, body)}).status_code == 401
    assert post(Authorization="Bearer other").status_code == 403
    for raw in (encode(payload | {"extra": True}), body[:-1] + b',"nonce":"duplicate"}', b" " * 16385):
        assert post(raw).status_code in (400, 403)
    service.spoken_reply.assert_called_once_with(payload)
    service.spoken_reply.return_value = {"unexpected": "x" * MAX_SPOKEN_BYTES}
    rejected = post()
    assert rejected.status_code == 502 and len(rejected.data) < 200
    assert "X-Ananta-Speech-Signature" not in rejected.headers
    assert (
        client.post(
            "/api/meet/v1/internal/dialog",
            data=body,
            headers={"X-Ananta-Dialog-Signature": request_signature(key, body)},
        ).status_code
        == 400
    )
