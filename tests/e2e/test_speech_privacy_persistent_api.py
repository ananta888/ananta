from __future__ import annotations

import time

from flask import Flask
from sqlmodel import Session

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.semantic_media_audit_repository import (
    SqlSemanticMediaAuditRepository,
)
from agent.repositories.speech_consent_repository import SpeechConsentRepository
from agent.routes.speech_evidence_consents import speech_evidence_consents_bp
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.user_session_tokens import issue_user_access_token
from tests.speech_evidence_support import consent_payload


def test_real_flask_consent_revoke_persists_fence_and_content_free_audit() -> None:
    """Exercise the authenticated API over the production SQL adapters."""

    username = "privacy-persistent-api-owner"
    payload = consent_payload("privacy-persistent-api")
    payload["tenant_id"] = username
    payload["owner_subject"] = username
    audit_repository = SqlSemanticMediaAuditRepository(db_engine=engine)
    recorder = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository),
        secret=b"persistent-privacy-api-audit-key" * 2,
    )
    service = SpeechEvidenceConsentService(
        SpeechConsentRepository(),
        audit=recorder,
    )

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(speech_evidence_consents_bp)
    app.extensions["speech_evidence_consent_service"] = service
    token = issue_user_access_token(username=username, role="admin")
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"

    granted = client.post(
        "/v1/voice/speech-evidence-consents",
        json=payload,
        headers={"Idempotency-Key": "persistent-privacy-consent-grant"},
    )
    assert granted.status_code == 201
    revoked = client.post(
        f"/v1/voice/speech-evidence-consents/{payload['consent_id']}/revoke",
        json={"contributor_id": payload["speaker_id"]},
        headers={
            "Idempotency-Key": "persistent-privacy-consent-revoke",
            "If-Match": '"1"',
        },
    )
    assert revoked.status_code == 200
    assert revoked.json["data"]["consent"]["state"] == "revoked"
    assert revoked.json["data"]["consent"]["revocation_epoch"] == 1

    with Session(engine) as session:
        persisted = session.get(SpeechEvidenceConsentDB, payload["consent_id"])
        assert persisted is not None
        assert persisted.state == "revoked"
        assert persisted.consent_version == 2
        assert persisted.revocation_epoch == 1

    audit_outbox = SqlSemanticMediaAuditOutbox(db_engine=engine)
    assert audit_outbox.pending_count() == 2
    dispatch = audit_outbox.dispatch_pending(limit=10)
    assert dispatch.delivered == 2
    assert dispatch.failed == 0
    assert dispatch.pending == 0
    assert audit_outbox.pending_count() == 0

    rows, cursor = audit_repository.page(
        tenant_digest=recorder.digest("tenant", username),
        scope_digest=recorder.digest(
            "scope",
            f"speech-consent:{payload['pair_id']}:{payload['direction']}",
        ),
        after_event_id=None,
        limit=10,
        now_ms=time.time_ns() // 1_000_000,
    )
    assert cursor is None
    assert [row.transition for row in rows] == ["granted", "revoked"]
    assert all(row.contract_ref and row.epoch in {1, 2} for row in rows)
    assert all(
        raw not in str(row.public())
        for row in rows
        for raw in (username, str(payload["speaker_id"]), str(payload["recipient_id"]))
    )
