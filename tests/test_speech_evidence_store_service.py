from __future__ import annotations

import hashlib

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.semantic_media_audit_repository import SqlSemanticMediaAuditRepository
from agent.repositories.speech_evidence import (
    SpeechEvidenceQuotas,
    SpeechEvidenceRepository,
)
from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.speech_evidence_store_service import SpeechEvidenceStoreError, SpeechEvidenceStoreService
from tests.speech_evidence_support import consent_payload, digest, identity, principal, stored_evidence


def _store_payload(
    store: SpeechEvidenceStoreService,
    consent,
    prefix: str,
    payload: bytes,
    *,
    provenance_digest: str | None = None,
):
    return store.store(
        principal(prefix),
        payload,
        claimed_content_digest=hashlib.sha256(payload).hexdigest(),
        provenance_digest=provenance_digest or digest(f"provenance-{prefix}-{payload.hex()}"),
        identity=identity(prefix, payload),
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


def test_quarantine_persists_ciphertext_only_and_duplicate_is_idempotent() -> None:
    prefix = "store-encrypted"
    payload = b"a private transcript that must not appear in metadata"
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"speech-evidence-audit-test-key" * 2,
    )
    consent_service, store, consent, first = stored_evidence(prefix, payload, audit=audit)
    second, created = store.store(
        principal(prefix),
        payload,
        claimed_content_digest=hashlib.sha256(payload).hexdigest(),
        provenance_digest=digest(f"provenance-{prefix}"),
        identity=identity(prefix, payload),
        evidence_class="transcript",
        data_class="transcript",
        grant="transcript_share",
        consent_id=consent.consent_id,
        consent_version=1,
        revocation_epoch=0,
        consent_digest=consent.consent_digest,
        speaker_id=consent.speaker_id,
        recipient_id=consent.recipient_id,
        direction=consent.direction,
        pair_id=consent.pair_id,
        session_id=consent.session_id,
        session_epoch=1,
        purpose=consent.purpose,
        retention_seconds=600,
    )

    assert not created and second.evidence_id == first.evidence_id
    with Session(engine) as session:
        row = session.get(SpeechEvidenceDB, first.evidence_id)
    assert row is not None and bytes(row.ciphertext) != payload
    assert payload.decode() not in str(first.public_dict())
    assert row.byte_count == len(payload)
    dispatch = SqlSemanticMediaAuditOutbox().dispatch_pending()
    assert dispatch.delivered == 1 and dispatch.pending == 0
    audit_rows, _ = SqlSemanticMediaAuditRepository().page(
        tenant_digest=audit.digest("tenant", principal(prefix).tenant_id),
        scope_digest=audit.digest("scope", f"semantic-media-session:{consent.session_id}"),
        after_event_id=None,
        limit=10,
        now_ms=1_000_000,
    )
    assert [(item.event_type, item.transition) for item in audit_rows] == [
        ("speech_evidence", "quarantined")
    ]


def test_digest_retention_and_implicit_training_fail_closed() -> None:
    prefix = "store-invalid"
    payload = b"payload"
    _, store, consent, _ = stored_evidence(prefix, payload)
    with pytest.raises(SpeechEvidenceStoreError) as error:
        store.store(
            principal(prefix),
            b"different",
            claimed_content_digest=hashlib.sha256(b"wrong").hexdigest(),
            provenance_digest=digest("provenance-invalid"),
            identity=identity(prefix, b"different"),
            evidence_class="transcript",
            data_class="transcript",
            grant="training",
            consent_id=consent.consent_id,
            consent_version=1,
            revocation_epoch=0,
            consent_digest=consent.consent_digest,
            speaker_id=consent.speaker_id,
            recipient_id=consent.recipient_id,
            direction=consent.direction,
            pair_id=consent.pair_id,
            session_id=consent.session_id,
            session_epoch=1,
            purpose=consent.purpose,
            retention_seconds=7000,
        )
    assert error.value.reason_code in {
        "speech_evidence_implicit_curation_forbidden",
        "speech_evidence_digest_mismatch",
    }

    with pytest.raises(SpeechEvidenceStoreError) as identity_error:
        store.store(
            principal(prefix),
            b"different",
            claimed_content_digest=hashlib.sha256(b"different").hexdigest(),
            provenance_digest=digest("provenance-invalid"),
            identity=identity("different-scope", b"different"),
            evidence_class="transcript",
            data_class="transcript",
            grant="transcript_share",
            consent_id=consent.consent_id,
            consent_version=1,
            revocation_epoch=0,
            consent_digest=consent.consent_digest,
            speaker_id=consent.speaker_id,
            recipient_id=consent.recipient_id,
            direction=consent.direction,
            pair_id=consent.pair_id,
            session_id=consent.session_id,
            session_epoch=1,
            purpose=consent.purpose,
            retention_seconds=600,
        )
    assert identity_error.value.reason_code == "speech_evidence_identity_scope_mismatch"


@pytest.mark.parametrize(
    ("quota_changes", "first", "second", "reason_code"),
    [
        ({"max_payload_bytes": 3}, None, b"four", "speech_evidence_payload_quota_exceeded"),
        (
            {"max_tenant_records": 1, "max_pair_records": 10},
            b"first",
            b"second",
            "speech_evidence_tenant_record_quota_exceeded",
        ),
        (
            {"max_tenant_records": 10, "max_pair_records": 1},
            b"first",
            b"second",
            "speech_evidence_pair_record_quota_exceeded",
        ),
        (
            {"max_tenant_bytes": 6, "max_pair_bytes": 100},
            b"1234",
            b"5678",
            "speech_evidence_tenant_byte_quota_exceeded",
        ),
        (
            {"max_tenant_bytes": 100, "max_pair_bytes": 6},
            b"1234",
            b"5678",
            "speech_evidence_pair_byte_quota_exceeded",
        ),
    ],
)
def test_payload_tenant_pair_byte_and_record_quotas_are_fail_closed(
    quota_changes: dict[str, int],
    first: bytes | None,
    second: bytes,
    reason_code: str,
) -> None:
    prefix = f"store-quota-{reason_code.rsplit('_', 2)[-2]}-{len(second)}"
    defaults = SpeechEvidenceQuotas().__dict__
    quotas = SpeechEvidenceQuotas(**{**defaults, **quota_changes})
    consent_service = SpeechEvidenceConsentService()
    consent = consent_service.grant(principal(prefix), consent_payload(prefix))
    store = SpeechEvidenceStoreService(
        repository=SpeechEvidenceRepository(quotas),
        consent=consent_service,
        digest_key=b"q" * 32,
    )
    if first is not None:
        assert _store_payload(store, consent, prefix, first)[1]

    with pytest.raises(SpeechEvidenceStoreError) as captured:
        _store_payload(store, consent, prefix, second)

    assert captured.value.reason_code == reason_code


class _StageFailureLineage:
    def stage(self, *_args, **_kwargs):
        raise RuntimeError("injected pre-commit lineage failure")


class _TrackingEncryption:
    def __init__(self) -> None:
        self.destroyed: list[tuple[str, str]] = []

    def encrypt(self, plaintext: bytes, **bindings):
        from ananta_contracts.speech_evidence_crypto import SpeechEvidenceCiphertext

        return SpeechEvidenceCiphertext(
            artifact_ref=str(bindings["artifact_ref"]),
            artifact_class=str(bindings["artifact_class"]),
            tenant_id=str(bindings["tenant_id"]),
            pair_id=str(bindings["pair_id"]),
            purpose=str(bindings["purpose"]),
            session_epoch=int(bindings["session_epoch"]),
            key_epoch=int(bindings["key_epoch"]),
            key_id="speech-key-partial-write-canary",
            content_digest=hashlib.sha256(b"scoped:" + plaintext).hexdigest(),
            nonce=b"n" * 12,
            ciphertext=plaintext + b"t" * 16,
        )

    def decrypt(self, envelope, *, security_mode="trusted_compute"):
        del security_mode
        return bytes(envelope.ciphertext[:-16])

    def destroy(self, key_id: str, *, tenant_id: str) -> bool:
        self.destroyed.append((key_id, tenant_id))
        return True


def test_precommit_partial_write_rolls_back_evidence_and_destroys_orphan_dek() -> None:
    prefix = "store-partial-write"
    consent_service = SpeechEvidenceConsentService()
    consent = consent_service.grant(principal(prefix), consent_payload(prefix))
    encryption = _TrackingEncryption()
    repository = SpeechEvidenceRepository(lineage=_StageFailureLineage())
    store = SpeechEvidenceStoreService(
        repository=repository,
        consent=consent_service,
        encryption=encryption,
        digest_key=b"p" * 32,
    )

    with pytest.raises(SpeechEvidenceStoreError) as captured:
        _store_payload(store, consent, prefix, b"partial-write-secret")

    assert captured.value.reason_code == "speech_evidence_write_failed"
    assert encryption.destroyed == [
        ("speech-key-partial-write-canary", principal(prefix).tenant_id)
    ]
    with Session(engine) as session:
        rows = session.exec(
            __import__("sqlmodel").select(SpeechEvidenceDB).where(
                SpeechEvidenceDB.tenant_id == principal(prefix).tenant_id
            )
        ).all()
    assert rows == []


class _FailOnceOutboxDelivery:
    def __init__(self) -> None:
        self.delegate = SpeechEvidenceLineageRepository()
        self.failed = False

    def stage(self, *args, **kwargs):
        return self.delegate.stage(*args, **kwargs)

    def process_outbox(self, **kwargs):
        if not self.failed:
            self.failed = True
            raise RuntimeError("injected crash after domain commit")
        return self.delegate.process_outbox(**kwargs)


def test_postcommit_outbox_crash_is_recoverable_without_destroying_persisted_key() -> None:
    prefix = "store-outbox-crash"
    consent_service = SpeechEvidenceConsentService()
    consent = consent_service.grant(principal(prefix), consent_payload(prefix))
    lineage = _FailOnceOutboxDelivery()
    repository = SpeechEvidenceRepository(lineage=lineage)
    store = SpeechEvidenceStoreService(
        repository=repository,
        consent=consent_service,
        digest_key=b"o" * 32,
    )
    payload = b"outbox-recovery-secret"

    with pytest.raises(SpeechEvidenceStoreError) as captured:
        _store_payload(store, consent, prefix, payload)
    assert captured.value.reason_code == "speech_evidence_lineage_delivery_pending"
    assert lineage.delegate.recover_pending(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
    ) == 1
    recovered, created = _store_payload(store, consent, prefix, payload)
    assert not created
    assert store._encryption.decrypt(  # noqa: SLF001 - verify the committed DEK survived recovery
        repository.encrypted(
            tenant_id=principal(prefix).tenant_id,
            owner_subject=principal(prefix).subject,
            evidence_id=recovered.evidence_id,
        ),
        security_mode="trusted_compute",
    ) == payload


def test_duplicate_after_key_loss_is_rejected_instead_of_silently_referenced() -> None:
    prefix = "store-key-loss"
    _consent_service, store, consent, record = stored_evidence(prefix, b"key-loss-secret")
    assert store._encryption.destroy(record.key_id, tenant_id=principal(prefix).tenant_id)  # noqa: SLF001

    with pytest.raises(SpeechEvidenceStoreError) as captured:
        _store_payload(
            store,
            consent,
            prefix,
            b"key-loss-secret",
            provenance_digest=digest(f"provenance-{prefix}"),
        )

    assert captured.value.reason_code == "speech_crypto_key_destroyed"
