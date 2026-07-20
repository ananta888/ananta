from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.speech_evidence import SpeechCurationTaskDB, SpeechEvidenceDB
from agent.services.ml_intern_speech_revocation_service import SpeechTrainingRevocationOutcome
from agent.services.speech_evidence_admission_policy import SpeechEvidenceAdmissionPolicy
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.speech_evidence_curation_task_service import SpeechEvidenceCurationTaskService
from agent.services.speech_evidence_encryption_port import AesGcmSpeechEvidenceEncryption
from agent.services.speech_evidence_key_service import SpeechEvidenceKeyService
from agent.services.speech_evidence_revocation_service import SpeechEvidenceRevocationService
from agent.services.speech_evidence_store_service import SpeechEvidenceStoreError, SpeechEvidenceStoreService
from tests.speech_evidence_support import (
    AllowAuthority,
    QueueRecorder,
    consent_payload,
    digest,
    identity,
    principal,
    stored_evidence,
)


@pytest.fixture
def concurrent_engine(monkeypatch, tmp_path):
    database = create_engine(
        f"sqlite:///{tmp_path / 'speech-atomic-races.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(database)
    from agent.repositories import speech_consent_repository, speech_evidence, speech_evidence_lineage
    from agent.services import (
        speech_evidence_admission_policy,
        speech_evidence_curation_task_service,
        speech_evidence_key_service,
        speech_evidence_revocation_service,
    )

    for module in (
        speech_consent_repository,
        speech_evidence,
        speech_evidence_lineage,
        speech_evidence_admission_policy,
        speech_evidence_curation_task_service,
        speech_evidence_key_service,
        speech_evidence_revocation_service,
    ):
        monkeypatch.setattr(module, "engine", database)
    return database


class _NoopTrainingFence:
    def fence_impact(self, _principal, _impact):
        return SpeechTrainingRevocationOutcome((), (), ())


class _BlockingEncryption:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.encrypted = threading.Event()
        self.release = threading.Event()
        self.destroyed: list[str] = []

    def encrypt(self, *args, **kwargs):
        envelope = self.delegate.encrypt(*args, **kwargs)
        self.encrypted.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("speech test encryption fence timed out")
        return envelope

    def decrypt(self, *args, **kwargs):
        return self.delegate.decrypt(*args, **kwargs)

    def destroy(self, key_id: str, *, tenant_id: str) -> bool:
        self.destroyed.append(key_id)
        return self.delegate.destroy(key_id, tenant_id=tenant_id)


def test_revoke_winning_before_store_claim_blocks_row_and_destroys_precreated_dek(
    concurrent_engine,
) -> None:
    prefix = "atomic-revoke-store"
    consent_service = SpeechEvidenceConsentService()
    consent = consent_service.grant(principal(prefix), consent_payload(prefix))
    encryption = _BlockingEncryption(
        AesGcmSpeechEvidenceEncryption(SpeechEvidenceKeyService(master_key=b"r" * 32))
    )
    store = SpeechEvidenceStoreService(
        consent=consent_service,
        encryption=encryption,
        digest_key=b"d" * 32,
    )
    payload = b"atomic store candidate"

    def persist():
        return store.store(
            principal(prefix),
            payload,
            claimed_content_digest=hashlib.sha256(payload).hexdigest(),
            provenance_digest=digest(f"provenance-{prefix}"),
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

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(persist)
        assert encryption.encrypted.wait(timeout=5)
        consent_service.revoke(
            principal(prefix),
            consent.consent_id,
            expected_version=consent.consent_version,
            contributor_id=consent.speaker_id,
        )
        encryption.release.set()
        with pytest.raises(SpeechEvidenceStoreError, match="speech_evidence_consent_stale"):
            pending.result(timeout=5)

    assert len(encryption.destroyed) == 1
    with Session(concurrent_engine) as session:
        assert session.exec(
            select(SpeechEvidenceDB).where(SpeechEvidenceDB.tenant_id == principal(prefix).tenant_id)
        ).all() == []


def _admitted(prefix: str):
    consent_service, store, consent, record = stored_evidence(prefix, b"atomic curation candidate")
    admission = SpeechEvidenceAdmissionPolicy(authority=AllowAuthority(), consent=consent_service).admit(
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
        quality_metrics={"duration_ms": 1000, "snr_db": 20.0, "clipping_ratio": 0.0, "silence_ratio": 0.1},
    )
    assert admission.decision == "admitted"
    return consent_service, store, consent, record, admission


class _BlockingQueue(QueueRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.enqueued = threading.Event()
        self.release = threading.Event()

    def ingest_task(self, **kwargs: object) -> None:
        super().ingest_task(**kwargs)
        self.enqueued.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("speech test queue fence timed out")


def test_revocation_after_curation_commit_cancels_before_queue_return(concurrent_engine) -> None:
    prefix = "atomic-revoke-curation"
    consent_service, store, consent, record, admission = _admitted(prefix)
    queue = _BlockingQueue()
    curation = SpeechEvidenceCurationTaskService(queue=queue, consent=consent_service)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            curation.create,
            principal(prefix),
            admission_digest=admission.admission_digest,
        )
        assert queue.enqueued.wait(timeout=5)
        SpeechEvidenceRevocationService(
            evidence=store._repository,  # noqa: SLF001
            consent=consent_service,
            encryption=store._encryption,  # noqa: SLF001
            training=_NoopTrainingFence(),
        ).revoke(
            principal(prefix),
            record.evidence_id,
            expected_consent_version=consent.consent_version,
            reason_code="speech_contributor_withdrawal",
            contributor_id=consent.speaker_id,
        )
        queue.release.set()
        task, created = pending.result(timeout=5)

    assert created
    with Session(concurrent_engine) as session:
        row = session.get(SpeechCurationTaskDB, task.task_id)
    assert row is not None and row.state == "cancelled" and row.fencing_token == 2


class _BlockingPublisher:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def publish(self, _result) -> bool:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("speech test publish fence timed out")
        return True


def test_publish_winning_database_fence_is_immediately_included_in_revocation_impact(
    concurrent_engine,
) -> None:
    prefix = "atomic-revoke-publish"
    consent_service, store, consent, record, admission = _admitted(prefix)
    publisher = _BlockingPublisher()
    curation = SpeechEvidenceCurationTaskService(
        queue=QueueRecorder(),
        result_port=publisher,
        consent=consent_service,
    )
    task, _ = curation.create(principal(prefix), admission_digest=admission.admission_digest)
    artifact_digest = digest(f"artifact-{prefix}")
    result = {
        "schema": "ananta.speech-evidence-curation-result.v1",
        "task_id": task.task_id,
        "admission_digest": task.admission_digest,
        "artifact_ref": task.artifact_publish_ref,
        "artifact_digest": artifact_digest,
        "consent_version": task.consent_version,
        "revocation_epoch": task.revocation_epoch,
        "fencing_token": task.fencing_token,
        "completed_at_ms": time.time_ns() // 1_000_000,
    }
    revocation = SpeechEvidenceRevocationService(
        evidence=store._repository,  # noqa: SLF001
        consent=consent_service,
        encryption=store._encryption,  # noqa: SLF001
        training=_NoopTrainingFence(),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        publishing = pool.submit(curation.authorize_result, principal(prefix), result)
        assert publisher.entered.wait(timeout=5)
        revoking = pool.submit(
            revocation.revoke,
            principal(prefix),
            record.evidence_id,
            expected_consent_version=consent.consent_version,
            reason_code="speech_contributor_withdrawal",
            contributor_id=consent.speaker_id,
        )
        assert not revoking.done()
        publisher.release.set()
        assert publishing.result(timeout=5).artifact_digest == artifact_digest
        revoked = revoking.result(timeout=5)

    result_node = next(
        node
        for node in revoked.impacted
        if node["kind"] == "reconciliation" and node["digest"] == artifact_digest
    )
    assert result_node["status"] == "active"
    from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository

    persisted = SpeechEvidenceLineageRepository().traverse(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        root_kind="reconciliation",
        root_digest=artifact_digest,
        direction="forward",
    )
    assert persisted.nodes[0]["status"] == "revoked"
