"""Actual Flask/task-authority/signing backchannel; explicitly synthetic storage."""

import time

import jwt
import pytest
from flask import Flask

from agent.bootstrap.meet_companion import configure_companion_task_grants
from agent.routes.meet import meet_bp
from ananta_contracts.meet_companion_task_grant import (
    REQUEST_SCHEMA,
    SIGNATURE_HEADER,
    request_signature,
    response_signature,
)
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from worker.meet_media.contract import encode
from worker.meet_media.contract import signature as legacy_signature

PATH = "/api/meet/v1/internal/companion/task-grant"
KEY = b"synthetic-task-companion-worker-key"


@pytest.fixture
def hub(assigned):
    f, service, payload, key = assigned
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub", ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED=True)
    app.register_blueprint(meet_bp)
    app.extensions["meet_media_worker_key"] = KEY
    configure_companion_task_grants(app, f.authority, service.issuer)
    app.extensions["meet_companion_task_grants"].clock = lambda: f.now
    return app, f, payload, key


def post(app, raw, **headers):
    return app.test_client().post(PATH, data=raw, headers={"Content-Type": "application/json",
                                 SIGNATURE_HEADER: request_signature(KEY, raw), **headers})


def packet(payload):
    return {"schema": REQUEST_SCHEMA, "nonce": "a" * 32, "sent_at": int(time.time()), "assignment": payload}


def test_actual_task_grant_http_signs_exact_request_and_current_task(hub):
    app, f, payload, key = hub
    raw = encode(packet(payload))
    response = post(app, raw)
    assert response.status_code == 200
    assert response.headers[SIGNATURE_HEADER] == response_signature(KEY, raw, response.data)
    assert response.headers["Cache-Control"] == "no-store"
    value = response.get_json()
    claims = jwt.decode(value["grant"], key.public_key(), algorithms=["EdDSA"],
                        issuer="https://hub.example.test", audience="ananta-meet-machine-v2")
    assert claims["taskId"] == "task" and claims["sessionId"] == "hub-session"
    assert value["controls"] == f.context["controls"]
    f.task.status = "cancelled"
    assert post(app, raw).status_code == 403


@pytest.mark.parametrize("header", ["", "0" * 64, "legacy"])
def test_missing_forged_or_legacy_direction_signature_is_denied(hub, header):
    app, _f, payload, _key = hub
    raw = encode(packet(payload))
    supplied = legacy_signature(KEY, raw) if header == "legacy" else header
    assert post(app, raw, **{SIGNATURE_HEADER: supplied}).status_code == 401


@pytest.mark.parametrize("raw", [b'[]', b'null', b'{"project_id":"a","project_id":"b"}', b'{', b'{}', b'x' * 1537])
def test_closed_payload_size_and_duplicate_keys_deny_without_fallback(hub, raw):
    app, _f, _payload, _key = hub
    response = post(app, raw)
    assert response.status_code == 403
    assert "grant" not in response.get_json()


def test_wrong_role_disabled_service_query_and_bearer_are_denied(hub):
    app, _f, payload, _key = hub
    raw = encode(packet(payload))
    assert post(app, raw, Authorization="Bearer synthetic").status_code == 403
    response = app.test_client().post(PATH + "?fallback=legacy", data=raw,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: request_signature(KEY, raw)})
    assert response.status_code == 403
    app.config["ROLE"] = "worker"
    assert post(app, raw).status_code == 403
    app.config["ROLE"] = "hub"
    app.extensions.pop("meet_companion_task_grants")
    assert post(app, raw).status_code == 404


def test_bootstrap_is_default_off_and_requires_actual_hub_ports(monkeypatch):
    monkeypatch.delenv("ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED", raising=False)
    app = Flask(__name__)
    configure_companion_task_grants(app, None, None)
    assert "meet_companion_task_grants" not in app.extensions
    app.config.update(ROLE="worker", ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED=True)
    with pytest.raises(ValueError):
        configure_companion_task_grants(app, None, None)


@pytest.mark.parametrize("patch", [{"sent_at": 1}, {"sent_at": True}, {"nonce": "unknown"},
                                   {"schema": "legacy"}, {"extra": True}])
def test_rpc_freshness_nonce_and_closed_envelope_are_enforced(hub, patch):
    app, _f, payload, _key = hub
    assert post(app, encode(packet(payload) | patch)).status_code == 403


def test_response_signature_binds_direction_request_nonce_and_body():
    one, two = encode(packet({})), encode(packet({}) | {"nonce": "b" * 32})
    signed = response_signature(KEY, one, b'{"controls":{}}')
    assert signed != response_signature(KEY, two, b'{"controls":{}}')
    assert signed != response_signature(KEY, one, b'{"controls":{"enabled":true}}')
    assert signed != request_signature(KEY, one)
