from __future__ import annotations

import base64
import hashlib
import json

from flask import Flask
from sqlmodel import Session

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceKeyDB
from agent.routes import voice_governance as voice_governance_routes
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_admission_policy import SpeechEvidenceAdmissionPolicy
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.speech_evidence_curation_task_service import SpeechEvidenceCurationTaskService
from agent.services.speech_evidence_store_service import SpeechEvidenceStoreService
from agent.services.user_session_tokens import issue_user_access_token
from tests.speech_evidence_support import (
    AllowAuthority,
    QueueRecorder,
    consent_payload,
    digest,
    identity,
    principal,
)


class _Audit:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._recorder = SemanticMediaAuditRecorder(
            SemanticMediaAuditService(InMemorySemanticMediaAuditRepository()),
            secret=b"speech-evidence-content-canary-audit-key",
        )

    def prepare_transition(self, **kwargs: object):
        event = self._recorder.prepare_transition(**kwargs)
        self.calls.append(event.public())
        return event


class _ResultPublisher:
    def publish(self, _result) -> bool:
        return True


def test_plaintext_dek_nonce_and_wrapped_key_canaries_never_reach_read_surfaces(
    monkeypatch,
    caplog,
) -> None:
    prefix = "crypto-canary"
    plaintext = b"PLAINTEXT-CANARY-9f5ef430-private-speech"
    audit = _Audit()
    consent_service = SpeechEvidenceConsentService(audit=audit)
    consent = consent_service.grant(principal(prefix), consent_payload(prefix))
    store = SpeechEvidenceStoreService(consent=consent_service, digest_key=b"c" * 32)
    record, created = store.store(
        principal(prefix),
        plaintext,
        claimed_content_digest=hashlib.sha256(plaintext).hexdigest(),
        provenance_digest=digest(f"provenance-{prefix}"),
        identity=identity(prefix, plaintext),
        evidence_class="transcript",
        data_class="transcript",
        grant="transcript_share",
        consent_id=consent.consent_id,
        consent_version=consent.consent_version,
        revocation_epoch=consent.revocation_epoch,
        consent_digest=consent.consent_digest,
        speaker_id=consent.speaker_id,
        recipient_id=consent.recipient_id,
        direction=consent.direction,
        pair_id=consent.pair_id,
        session_id=consent.session_id,
        session_epoch=consent.session_epoch,
        purpose=consent.purpose,
        retention_seconds=600,
    )
    assert created
    envelope = store._repository.encrypted(  # noqa: SLF001 - security canary extraction
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        evidence_id=record.evidence_id,
    )
    keys = store._encryption._keys  # noqa: SLF001 - inspect the real crypto boundary
    dek = keys.unwrap(
        envelope.key_id,
        tenant_id=envelope.tenant_id,
        pair_id=envelope.pair_id,
        purpose=envelope.purpose,
        artifact_class=envelope.artifact_class,
        artifact_ref=envelope.artifact_ref,
        key_epoch=envelope.key_epoch,
    )
    with Session(engine) as session:
        key_row = session.get(SpeechEvidenceKeyDB, envelope.key_id)
    assert key_row is not None and key_row.wrapped_dek and key_row.wrapping_nonce

    admission = SpeechEvidenceAdmissionPolicy(
        authority=AllowAuthority(), consent=consent_service
    ).admit(
        principal(prefix),
        record.evidence_id,
        peer_id=consent.speaker_id,
        speaker_id=consent.speaker_id,
        recipient_id=consent.recipient_id,
        direction=consent.direction,
        data_class="transcript",
        purpose=consent.purpose,
        evidence_signature=digest(f"signature-{prefix}"),
        provenance_digest=digest(f"provenance-{prefix}"),
        source_digest=record.source_digest,
        speaker_scope_digest=record.speaker_scope_digest,
        transcript_authority="human_verified",
        quality_metrics={"duration_ms": 1000, "snr_db": 22.0, "clipping_ratio": 0.0, "silence_ratio": 0.1},
    )
    queue = QueueRecorder()
    curation = SpeechEvidenceCurationTaskService(
        queue=queue,
        result_port=_ResultPublisher(),
        consent=consent_service,
    )
    task, _ = curation.create(principal(prefix), admission_digest=admission.admission_digest)

    class _ApiCuration:
        def create(self, _principal, *, admission_digest: str):
            assert admission_digest == admission.admission_digest
            return task, True

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(voice_governance_routes.voice_governance_bp)
    monkeypatch.setattr(
        voice_governance_routes,
        "get_speech_evidence_curation_task_service",
        lambda: _ApiCuration(),
    )
    token = issue_user_access_token(username="crypto-canary-owner", role="admin")
    response = app.test_client().post(
        "/v1/voice/evidence-curation-tasks",
        json={"admission_digest": admission.admission_digest, "confirmed": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201

    surfaces = {
        "database_read_model": record.public_dict(),
        "api": response.get_json(),
        "hub_task": queue.calls,
        "log": caplog.text,
        "audit": audit.calls,
        "metrics": admission.public_dict(),
    }
    rendered = json.dumps(surfaces, sort_keys=True, default=str)
    secret_encodings = {
        plaintext.decode(),
        plaintext.hex(),
        base64.b64encode(plaintext).decode(),
        dek.hex(),
        base64.b64encode(dek).decode(),
        envelope.nonce.hex(),
        base64.b64encode(envelope.nonce).decode(),
        bytes(key_row.wrapping_nonce).hex(),
        base64.b64encode(bytes(key_row.wrapping_nonce)).decode(),
        bytes(key_row.wrapped_dek).hex(),
        base64.b64encode(bytes(key_row.wrapped_dek)).decode(),
        envelope.key_id,
    }
    assert all(secret not in rendered for secret in secret_encodings)
