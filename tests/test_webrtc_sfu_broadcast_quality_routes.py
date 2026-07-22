from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.webrtc_sfu_broadcast_quality import webrtc_sfu_broadcast_quality_bp
from agent.services.sfu_receiver_quality_ingestion_service import (
    SfuReceiverQualityAuthority,
    SfuReceiverQualityIngestionService,
    build_sfu_receiver_quality_validator,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/webrtc/receiver_quality_observation/valid_privacy_bounded.v1.json").read_text(
        encoding="utf-8"
    )
)
NOW = datetime.fromisoformat(FIXTURE["validation_context"]["now"].replace("Z", "+00:00")).timestamp()


class _Authority:
    def resolve(self, command):
        scope = FIXTURE["validation_context"]["active_scope"]
        return SfuReceiverQualityAuthority(
            tenant_ref=scope["tenant_ref"], room_ref=scope["room_ref"],
            subscriber_ref=scope["subscriber_ref"], subscription_ref=command.subscription_ref,
            publication_ref=scope["publication_ref"],
            browser_instance_pseudonym=scope["browser_instance_pseudonym"],
            membership_epoch=command.membership_epoch, route_epoch=scope["route_epoch"],
            allowed_layer=scope["allowed_layer"],
        )


def _app():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(webrtc_sfu_broadcast_quality_bp)
    app.extensions["sfu_receiver_quality_ingestion_service"] = SfuReceiverQualityIngestionService(
        authority=_Authority(),
        validator=build_sfu_receiver_quality_validator(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    return app


def _headers():
    token = generate_token(
        {"sub": "subscriber-fixture-01", "tenant_id": "tenant-fixture", "role": "user"},
        settings.secret_key,
        expires_in=3600,
    )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _url(subscription="subscription-fixture-01"):
    return f"/v1/semantic-media/sfu/quality-observations/{subscription}?session_id=session-fixture&membership_epoch=7"


def test_route_requires_hub_user_auth_and_returns_non_authoritative_acceptance():
    client = _app().test_client()
    assert client.post(_url(), data=json.dumps(FIXTURE["instance"])).status_code == 401
    response = client.post(_url(), data=json.dumps(FIXTURE["instance"]), headers=_headers())
    assert response.status_code == 202
    assert response.get_json() == {
        "ok": True,
        "status": "accepted",
        "reason_code": "ok",
        "retained_report_count": 1,
        "sequence": 41,
        "gap_count": 0,
        "authoritative": False,
        "authorization_effect": "none",
    }


def test_route_rejects_cross_subscriber_and_oversize_with_stable_codes():
    client = _app().test_client()
    forged = copy.deepcopy(FIXTURE["instance"])
    forged["subscriber_ref"] = "subscriber-other-01"
    response = client.post(_url(), data=json.dumps(forged), headers=_headers())
    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "cross_subscriber_observation"

    response = client.post(_url(), data=b"{" + b" " * 8_193, headers=_headers())
    assert response.status_code == 413
    assert response.get_json()["reason_code"] == "report_bytes_exceeded"


def test_route_reads_bounded_window_and_cleans_it_on_revoke_or_leave():
    client = _app().test_client()
    client.post(_url(), data=json.dumps(FIXTURE["instance"]), headers=_headers())
    response = client.get(_url(), headers=_headers())
    assert response.status_code == 200
    assert response.get_json()["authoritative"] is False
    assert len(response.get_json()["reports"]) == 1

    response = client.delete(_url(), headers=_headers())
    assert response.get_json() == {
        "ok": True,
        "reason_code": "quality_subscription_cleared",
        "removed": 1,
    }
    response = client.delete(
        "/v1/semantic-media/sfu/quality-observations?session_id=session-fixture&membership_epoch=7",
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.get_json()["reason_code"] == "quality_participant_left"


def test_route_fails_closed_when_service_or_scope_is_missing():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(webrtc_sfu_broadcast_quality_bp)
    client = app.test_client()
    response = client.post(
        "/v1/semantic-media/sfu/quality-observations/subscription-fixture-01",
        data=json.dumps(FIXTURE["instance"]),
        headers=_headers(),
    )
    assert response.status_code == 400
    assert response.get_json()["reason_code"] == "quality_membership_epoch_invalid"
