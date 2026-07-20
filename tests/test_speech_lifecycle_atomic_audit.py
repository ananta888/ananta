from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    SemanticMediaAuditEventDB,
    SemanticMediaAuditOutboxDB,
    SpeechAdaptationJobDB,
    SpeechDatasetManifestDB,
    SpeechEvidenceDB,
    SpeechEvidenceRevocationDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_adaptation import SqlSpeechAdaptationDecisionStore
from agent.repositories.speech_reconciliation import SpeechReconciliationRepository
from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditError,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_program_evidence import assert_content_free
from agent.services.speech_adaptation_job_service import (
    SpeechAdmissionDecision,
    SpeechPrincipal,
)
from agent.services.speech_evidence_retention_cleanup_service import (
    SpeechEvidenceRetentionCleanupService,
)
from tests.speech_adaptation_support import digest as adaptation_digest
from tests.speech_adaptation_support import speech_job
from tests.speech_evidence_support import (
    AcceptPublisher,
    AllowDatasetConsent,
    manifest_record,
    stored_evidence,
)
from tests.speech_evidence_support import (
    digest as evidence_digest,
)
from tests.speech_evidence_support import (
    principal as evidence_principal,
)
from tests.test_speech_reconciliation_repository import _spec


def _audit(now_ms: int = 1_000_000) -> SemanticMediaAuditRecorder:
    return SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: now_ms,
        ),
        secret=b"speech-lifecycle-atomic-audit-test-key" * 2,
    )


def test_reconciliation_rolls_back_when_audit_command_cannot_be_staged(monkeypatch) -> None:
    spec = _spec("atomic-audit-rollback")
    repository = SpeechReconciliationRepository(audit=_audit())

    def fail_enqueue(_session, _event):
        raise SemanticMediaAuditError("audit_outbox_unavailable", status_code=503)

    monkeypatch.setattr(
        SqlSemanticMediaAuditOutbox,
        "enqueue_in_session",
        staticmethod(fail_enqueue),
    )
    with pytest.raises(SemanticMediaAuditError, match="audit_outbox_unavailable"):
        repository.create_job(spec, now_ms=1_000_000)
    assert repository.get_job(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=spec.job_id,
    ) is None


def test_adaptation_decision_rolls_back_when_audit_command_cannot_be_staged(monkeypatch) -> None:
    job = speech_job(
        job_id="speech-job-atomic-audit-rollback",
        artifact_id="speech-adapter-atomic-audit-rollback",
    )
    principal = SpeechPrincipal("tenant-atomic-adaptation", "owner-atomic-adaptation")
    decision = SpeechAdmissionDecision(
        job.job_id,
        "speech-task-atomic-adaptation",
        "queued",
        "speech_training_admitted",
        job,
        adaptation_digest("speech-adaptation-atomic-request"),
    )
    store = SqlSpeechAdaptationDecisionStore(audit=_audit())

    def fail_enqueue(_session, _event):
        raise SemanticMediaAuditError("audit_outbox_unavailable", status_code=503)

    monkeypatch.setattr(
        SqlSemanticMediaAuditOutbox,
        "enqueue_in_session",
        staticmethod(fail_enqueue),
    )
    with pytest.raises(SemanticMediaAuditError, match="audit_outbox_unavailable"):
        store.create(
            principal,
            idempotency_digest=adaptation_digest("speech-adaptation-atomic-key"),
            decision=decision,
        )
    with Session(engine) as session:
        assert session.get(SpeechAdaptationJobDB, job.job_id) is None


def test_dataset_authority_rolls_back_when_audit_command_cannot_be_staged(monkeypatch) -> None:
    label = "speech-dataset-atomic-audit-rollback"
    principal = evidence_principal(label)
    builder = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(),
        consent_authority=AllowDatasetConsent(),
        audit=_audit(),
    )

    def fail_enqueue(_session, _event):
        raise SemanticMediaAuditError("audit_outbox_unavailable", status_code=503)

    monkeypatch.setattr(
        SqlSemanticMediaAuditOutbox,
        "enqueue_in_session",
        staticmethod(fail_enqueue),
    )
    with pytest.raises(SemanticMediaAuditError, match="audit_outbox_unavailable"):
        builder.build(
            principal,
            dataset_id=label,
            records=[manifest_record(label)],
            curation_report_digest=evidence_digest(f"report-{label}"),
        )
    with Session(engine) as session:
        rows = session.exec(
            select(SpeechDatasetManifestDB).where(
                SpeechDatasetManifestDB.tenant_id == principal.tenant_id,
                SpeechDatasetManifestDB.owner_subject == principal.subject,
                SpeechDatasetManifestDB.dataset_id == label,
            )
        ).all()
    assert rows == []


def test_retention_commits_ciphertext_tombstone_and_content_free_audit_together() -> None:
    label = "speech-retention-atomic-audit"
    _consents, _store, _grant, record = stored_evidence(label, b"must never enter audit")
    audit = _audit(record.expires_at_ms + 1)
    summary = SpeechEvidenceRetentionCleanupService(audit=audit).run_once(
        limit=1,
        now_ms=record.expires_at_ms + 1,
        principal=evidence_principal(label),
    )
    assert summary.completed == 1
    with Session(engine) as session:
        evidence = session.get(SpeechEvidenceDB, record.evidence_id)
        tombstone = session.exec(
            select(SpeechEvidenceRevocationDB).where(
                SpeechEvidenceRevocationDB.tenant_id == record.tenant_id,
                SpeechEvidenceRevocationDB.evidence_digest == record.content_digest,
            )
        ).one()
        commands = session.exec(
            select(SemanticMediaAuditOutboxDB)
            .where(
                SemanticMediaAuditOutboxDB.job_ref
                == audit.digest("job", record.evidence_id)
            )
            .order_by(SemanticMediaAuditOutboxDB.created_at_ms, SemanticMediaAuditOutboxDB.id)
        ).all()
    assert evidence is not None and evidence.state == "deleted" and not evidence.ciphertext
    assert tombstone.reason_code == "speech_evidence_retention_expired"
    assert {command.transition for command in commands} == {
        "cleanup_staged",
        "cleanup_pending",
        "deleted",
    }
    assert "must never enter audit" not in str(commands)


def test_reconciliation_multi_hub_replay_dispatches_exactly_one_content_free_event() -> None:
    spec = _spec("atomic-audit-multi-hub")
    audit = _audit()

    def create():
        return SpeechReconciliationRepository(audit=audit).create_job(spec, now_ms=1_000_000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: create(), range(2)))
    assert sorted(created for _record, created in outcomes) == [False, True]

    with Session(engine) as session:
        pending = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.job_ref == audit.digest("job", spec.job_id)
            )
        ).all()
        assert len(pending) == 1
        public = {
            "event_id": pending[0].event_id,
            "tenant_digest": pending[0].tenant_digest,
            "scope_digest": pending[0].scope_digest,
            "event_type": pending[0].event_type,
            "transition": pending[0].transition,
            "reason_code": pending[0].reason_code,
            "epoch": pending[0].epoch,
            "contract_ref": pending[0].contract_ref,
            "lease_ref": pending[0].lease_ref,
            "job_ref": pending[0].job_ref,
            "created_at_ms": pending[0].created_at_ms,
            "expires_at_ms": pending[0].expires_at_ms,
        }
        assert_content_free(public)

    failed_dispatcher = SqlSemanticMediaAuditOutbox(clock_ms=lambda: 1_000_001)
    original = failed_dispatcher._dispatch_one

    def fail_dispatch(_outbox_id: str):
        raise SemanticMediaAuditError("audit_dispatch_failed", status_code=503)

    failed_dispatcher._dispatch_one = fail_dispatch
    failed = failed_dispatcher.dispatch_pending(limit=10)
    assert (failed.attempted, failed.failed, failed.pending) == (1, 1, 1)
    failed_dispatcher._dispatch_one = original
    delivered = failed_dispatcher.dispatch_pending(limit=10)
    assert (delivered.delivered, delivered.pending) == (1, 0)

    # A later Hub replay observes the durable mutation and does not recreate
    # either an outbox command or a second final audit row.
    replay, created = create()
    assert not created and replay.id == spec.job_id
    with Session(engine) as session:
        final = session.exec(
            select(SemanticMediaAuditEventDB).where(
                SemanticMediaAuditEventDB.job_ref == audit.digest("job", spec.job_id)
            )
        ).all()
        pending = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.job_ref == audit.digest("job", spec.job_id)
            )
        ).all()
    assert len(final) == 1
    assert pending == []
