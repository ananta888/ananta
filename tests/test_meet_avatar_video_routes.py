"""Headless real Flask avatar boundaries with explicit synthetic authentication."""

import time
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
from ananta_contracts.meet_avatar_video import MAX_AVATAR_VIDEO_BYTES, video_request_signature, video_response_signature
from ananta_contracts.meet_dialog import request_signature
from tests.test_meet_avatar_video_contract import packet
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


def test_actual_video_callback_checks_separate_signature_exact_body_and_no_store():
    app, runtime = application()
    request, result = packet()
    request["sent_at"] = int(time.time())
    request["binding"]["deadline_ms"] = (int(time.time()) + 60) * 1000
    result.update(binding=request["binding"])
    runtime.avatar_video.return_value = result
    key, client = app.extensions["meet_media_worker_key"], app.test_client()

    def post(raw, **headers):
        return client.post(
            "/api/meet/v1/internal/dialog/avatar-video",
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Ananta-Avatar-Video-Signature": video_request_signature(key, raw),
                **headers,
            },
        )

    raw = encode(request)
    response = post(raw)
    assert response.status_code == 200
    assert response.headers["X-Ananta-Avatar-Video-Signature"] == video_response_signature(key, raw, response.data)
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    runtime.avatar_video.assert_called_once_with(request)
    for invalid in (
        encode(request | {"url": "https://video"}),
        encode(request | {"sent_at": 1}),
        raw[:-1] + b',"nonce":"b"}',
    ):
        assert post(invalid).status_code == 400
    assert post(raw, Authorization="Bearer synthetic-user").status_code == 403
    assert post(raw, **{"X-Ananta-Avatar-Video-Signature": request_signature(key, raw)}).status_code == 401
    assert post(b" " * 16385).status_code == 403
    runtime.avatar_video.assert_called_once()
    runtime.avatar_video.return_value = {"oversize": "x" * MAX_AVATAR_VIDEO_BYTES}
    assert post(raw).status_code == 502
