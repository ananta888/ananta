from __future__ import annotations

import logging

import pytest
from flask import Flask

from agent.routes import semantic_sfu_admission as sfu_routes
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_observability_policy import (
    EVENT_RULES,
    ObservabilityPolicyError,
    sanitize_observability_event,
    scope_digest,
)
from agent.services.user_session_tokens import issue_user_access_token


@pytest.mark.parametrize(
    "field",
    ["audio", "image_pixels", "transcript", "residual_features", "encryption_key", "local_path", "partner_id"],
)
def test_content_and_identity_fields_are_forbidden(field: str) -> None:
    with pytest.raises(ObservabilityPolicyError, match="field_not_allowed"):
        sanitize_observability_event("semantic_transport", {field: "KNOWN-SECRET-TEXT"})


def test_allowlist_enforces_reason_size_and_scalar_contract() -> None:
    event = sanitize_observability_event(
        "semantic_transport",
        {"reason_code": "accepted", "state": "connected", "item_count": 2, "duration_ms": 8.5},
    )
    assert event["reason_code"] == "accepted"
    with pytest.raises(ObservabilityPolicyError, match="unknown_public_reason_code"):
        sanitize_observability_event("semantic_transport", {"reason_code": "raw failure text"})
    with pytest.raises(ObservabilityPolicyError, match="unsafe_observability_value"):
        sanitize_observability_event("semantic_transport", {"state": "x" * 97})
    assert all(rule.max_distinct_values_per_window <= 128 for rule in EVENT_RULES.values())


def test_known_secret_never_reaches_log_event_trace_audit_or_metric(caplog) -> None:
    secret = "KNOWN-SECRET-TEXT"
    rejected_channels: list[dict] = []
    for channel in ("event", "trace", "audit", "metric"):
        try:
            rejected_channels.append(
                sanitize_observability_event("semantic_control", {"payload": secret, "state": channel})
            )
        except ObservabilityPolicyError:
            logging.getLogger("semantic_media").info("semantic payload rejected for %s", channel)
    captured = caplog.text + repr(rejected_channels)
    assert secret not in captured


def test_scope_digest_is_epoch_bound_non_plaintext_and_scope_separated() -> None:
    secret = b"test-only-observability-key"
    first = scope_digest("pair-a", secret=secret, now_seconds=3600)
    same = scope_digest("pair-a", secret=secret, now_seconds=7199)
    next_epoch = scope_digest("pair-a", secret=secret, now_seconds=7200)
    other = scope_digest("pair-b", secret=secret, now_seconds=3600)
    assert first == same
    assert first != next_epoch != other
    assert "pair-a" not in first


def test_real_sfu_route_audit_sink_never_receives_room_or_identity_canary(monkeypatch) -> None:
    canary = "KNOWN-PARTNER-ROOM-CANARY"
    app = Flask(__name__)
    app.config["TESTING"] = True
    audit_service = SemanticMediaAuditService(InMemorySemanticMediaAuditRepository())
    app.extensions["semantic_media_audit_recorder"] = SemanticMediaAuditRecorder(
        audit_service,
        secret=b"test-only-observability-route-key-32-bytes",
    )
    app.register_blueprint(sfu_routes.semantic_sfu_admission_bp)

    class Service:
        def join(self, _body, *, actor_id, tenant_id):
            assert actor_id == "audit-user" and tenant_id == "audit-user"
            return {
                "room_id": canary,
                "membership_epoch": 3,
                "revision": 2,
                "reason_code": "accepted",
            }

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(sfu_routes, "get_semantic_sfu_admission_service", Service)
    monkeypatch.setattr(
        sfu_routes,
        "log_audit",
        lambda event, fields: captured.append((event, dict(fields))),
    )
    token = issue_user_access_token(username="audit-user", role="admin")
    response = app.test_client().post(
        "/v1/semantic-media/sfu/admissions/join",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert captured[0][0] == "semantic_sfu_admission_granted"
    assert "scope_digest" in captured[0][1]
    assert canary not in repr(captured)
    assert "audit-user" not in repr(captured)
