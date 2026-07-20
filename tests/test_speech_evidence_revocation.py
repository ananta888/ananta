from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.database import engine
from agent.db_models import SemanticMediaAuditEventDB, SemanticMediaAuditOutboxDB, SpeechEvidenceDB
from agent.db_models.ml_intern_training import MlInternSpeechAdapterDB, MlInternTrainingJobDB
from agent.db_models.speech_evidence import SpeechEvidenceRevocationDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_lineage import (
    LINEAGE_KINDS,
    SpeechLineageEdge,
    SpeechLineageNode,
)
from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
from agent.services.ml_intern_speech_revocation_service import SpeechTrainingRevocationOutcome
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_revocation_service import (
    SpeechEvidenceRevocationError,
    SpeechEvidenceRevocationService,
)
from tests.speech_evidence_support import digest, principal, stored_evidence


def _audit() -> SemanticMediaAuditRecorder:
    return SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: 1_000_000,
        ),
        secret=b"speech-revocation-audit-test-key" * 2,
    )


def test_revocation_propagates_to_jobs_adapters_keys_and_content_free_tombstone() -> None:
    prefix = "revocation-impact"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"revocable evidence")
    job_digest = digest(f"job-{prefix}")
    adapter_digest = digest(f"adapter-{prefix}")
    get_ml_intern_speech_lineage_service().publish(
        principal(prefix),
        nodes=(
            SpeechLineageNode("evidence", record.content_digest, consent_id=consent.consent_id),
            SpeechLineageNode("job", job_digest, consent_id=consent.consent_id),
            SpeechLineageNode("adapter", adapter_digest, consent_id=consent.consent_id),
        ),
        edges=(
            SpeechLineageEdge("evidence", record.content_digest, "job", job_digest, "trained_by"),
            SpeechLineageEdge("job", job_digest, "adapter", adapter_digest, "published_as"),
        ),
    )
    with Session(engine) as session:
        session.add(
            MlInternTrainingJobDB(
                tenant_id=principal(prefix).tenant_id,
                owner_subject=principal(prefix).subject,
                task_id=f"task-{prefix}",
                idempotency_key_digest=digest(f"idempotency-{prefix}"),
                request_digest=job_digest,
                status="queued",
            )
        )
        session.add(
            MlInternSpeechAdapterDB(
                id=f"speech-adapter-{prefix}",
                version="v1",
                tenant_id=principal(prefix).tenant_id,
                owner_subject=principal(prefix).subject,
                pair_id=consent.pair_id,
                direction=consent.direction,
                speaker_digest=record.speaker_scope_digest,
                scope_digest=consent.scope_digest,
                base_model_id="base-model",
                base_model_digest=digest("base-model"),
                backend="test-backend",
                backend_digest=digest("backend"),
                dataset_digest=digest("dataset"),
                split_digest=digest("split"),
                evaluation_report_digest=digest("eval"),
                consent_digest=consent.consent_digest,
                consent_expires_at_ms=consent.expires_at_ms,
                artifact_ref=f"artifact://speech-adapters/{prefix}",
                artifact_sha256=adapter_digest,
                artifact_size_bytes=128,
                expires_at_ms=consent.expires_at_ms,
                status="approved",
            )
        )
        session.commit()
    result = SpeechEvidenceRevocationService().revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=1,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    with Session(engine) as session:
        job = session.exec(
            __import__("sqlmodel")
            .select(MlInternTrainingJobDB)
            .where(MlInternTrainingJobDB.request_digest == job_digest)
        ).one()
        adapter = session.get(MlInternSpeechAdapterDB, f"speech-adapter-{prefix}")
    assert result.key_destroyed and result.fenced_jobs == (job_digest,)
    assert result.fenced_adapters == (adapter_digest,)
    assert job.status == "cancelled"
    assert adapter is not None and adapter.status == "revoked"
    assert all("revocable evidence" not in str(node) for node in result.impacted)


def test_remote_ack_is_bound_to_signed_request_and_never_claims_peer_deletion_early() -> None:
    prefix = "revocation-remote"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"remote evidence")
    service = SpeechEvidenceRevocationService()
    result = service.revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=1,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    assert result.remote_state == "not_requested"
    request_digest = digest(f"request-{prefix}")
    service.stage_remote_request(
        principal(prefix),
        evidence_digest=record.content_digest,
        request_digest=request_digest,
        signature_verified=True,
    )
    service.acknowledge_remote(
        principal(prefix),
        evidence_digest=record.content_digest,
        request_digest=request_digest,
        ack_digest=digest(f"ack-{prefix}"),
        signature_verified=True,
    )


def test_revocation_and_remote_states_dispatch_one_content_free_event_each() -> None:
    prefix = "revocation-atomic-audit"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"private revoke payload")
    service = SpeechEvidenceRevocationService(audit=_audit(), training=_NoopTrainingFence())

    first = service.revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=consent.consent_version,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    replay = service.revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=consent.consent_version,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    request_digest = digest(f"request-{prefix}")
    service.stage_remote_request(
        principal(prefix),
        evidence_digest=record.content_digest,
        request_digest=request_digest,
        signature_verified=True,
    )
    service.acknowledge_remote(
        principal(prefix),
        evidence_digest=record.content_digest,
        request_digest=request_digest,
        ack_digest=digest(f"ack-{prefix}"),
        signature_verified=True,
    )

    assert replay.idempotent_replay and replay.impact_digest == first.impact_digest
    dispatched = SqlSemanticMediaAuditOutbox().dispatch_pending(limit=10)
    assert (dispatched.delivered, dispatched.failed, dispatched.pending) == (3, 0, 0)
    with Session(engine) as session:
        events = session.exec(select(SemanticMediaAuditEventDB)).all()
    assert len(events) == 3
    assert {event.transition for event in events} == {
        "revoked",
        "remote_revocation_requested",
        "remote_revocation_acknowledged",
    }
    assert "private revoke payload" not in repr(events)


def test_revocation_state_rolls_back_when_audit_outbox_insert_fails() -> None:
    prefix = "revocation-audit-rollback"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"rollback revoke payload")
    service = SpeechEvidenceRevocationService(audit=_audit(), training=_NoopTrainingFence())
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_revocation_audit BEFORE INSERT ON semantic_media_audit_outbox "
            "BEGIN SELECT RAISE(FAIL, 'injected audit outbox outage'); END"
        )
    try:
        with pytest.raises(Exception, match="audit outbox outage"):
            service.revoke(
                principal(prefix),
                record.evidence_id,
                expected_consent_version=consent.consent_version,
                reason_code="speech_contributor_withdrawal",
                contributor_id=consent.speaker_id,
            )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_revocation_audit")

    with Session(engine) as session:
        evidence = session.get(SpeechEvidenceDB, record.evidence_id)
        assert evidence is not None and evidence.state == "quarantined"
        assert session.exec(select(SemanticMediaAuditOutboxDB)).all() == []

    resumed = service.revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=consent.consent_version,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    assert resumed.key_destroyed
    assert SqlSemanticMediaAuditOutbox().dispatch_pending(limit=10).delivered == 1


class _NoopTrainingFence:
    def fence_impact(self, _principal, _impact):
        return SpeechTrainingRevocationOutcome((), (), ())


class _FailFirstKeyDestroy:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    def destroy(self, key_id: str, *, tenant_id: str) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("injected KMS outage")
        return self.delegate.destroy(key_id, tenant_id=tenant_id)


def test_revocation_resumes_after_partial_key_cleanup_failure() -> None:
    prefix = "revocation-partial-cleanup"
    consent_service, store, consent, record = stored_evidence(prefix, b"partial cleanup")
    encryption = _FailFirstKeyDestroy(store._encryption)  # noqa: SLF001 - fault-injection port
    service = SpeechEvidenceRevocationService(
        evidence=store._repository,  # noqa: SLF001 - same persisted quarantine
        consent=consent_service,
        encryption=encryption,
        training=_NoopTrainingFence(),
    )

    with pytest.raises(RuntimeError, match="injected KMS outage"):
        service.revoke(
            principal(prefix),
            record.evidence_id,
            expected_consent_version=consent.consent_version,
            reason_code="speech_contributor_withdrawal",
            contributor_id=consent.speaker_id,
        )

    resumed = service.revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=consent.consent_version,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    assert resumed.key_destroyed and encryption.calls == 2
    assert consent_service.get(principal(prefix), consent.consent_id).state == "revoked"


def test_impact_and_tombstone_cover_every_transitive_artifact_kind() -> None:
    prefix = "revocation-all-impact-kinds"
    consent_service, store, consent, record = stored_evidence(prefix, b"all impact kinds")
    kinds = [
        "evidence",
        "manifest",
        "split",
        "reconciliation",
        "job",
        "checkpoint",
        "evaluation",
        "model",
        "adapter",
        "export",
        "receipt",
    ]
    nodes = [SpeechLineageNode("evidence", record.content_digest, consent_id=consent.consent_id)]
    nodes.extend(
        SpeechLineageNode(kind, digest(f"{prefix}-{kind}"), consent_id=consent.consent_id)
        for kind in kinds[1:]
    )
    get_ml_intern_speech_lineage_service().publish(
        principal(prefix),
        nodes=tuple(nodes),
        edges=tuple(
            SpeechLineageEdge(
                nodes[index].kind,
                nodes[index].digest,
                nodes[index + 1].kind,
                nodes[index + 1].digest,
                "derived_from",
            )
            for index in range(len(nodes) - 1)
        ),
    )

    result = SpeechEvidenceRevocationService(
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

    assert {node["kind"] for node in result.impacted} == set(LINEAGE_KINDS)
    assert result.remote_state == "unresolved"
    page = get_ml_intern_speech_lineage_service().forward(
        principal(prefix),
        root_kind="evidence",
        root_digest=record.content_digest,
        limit=100,
    )
    assert all(node["status"] == "revoked" for node in page.nodes)


def test_parallel_revocation_joins_monotonic_consent_and_one_tombstone(monkeypatch, tmp_path) -> None:
    # The suite-wide in-memory SQLite fixture intentionally shares one DBAPI
    # connection and cannot model two Hub connections. Use a file database so
    # the test exercises real concurrent CAS/writer serialization.
    concurrent_engine = create_engine(
        f"sqlite:///{tmp_path / 'parallel-revocation.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(concurrent_engine)
    from agent.repositories import speech_consent_repository, speech_evidence, speech_evidence_lineage
    from agent.services import (
        speech_evidence_curation_task_service,
        speech_evidence_key_service,
        speech_evidence_revocation_service,
    )

    for module in (
        speech_consent_repository,
        speech_evidence,
        speech_evidence_lineage,
        speech_evidence_curation_task_service,
        speech_evidence_key_service,
        speech_evidence_revocation_service,
    ):
        monkeypatch.setattr(module, "engine", concurrent_engine)
    prefix = "revocation-parallel"
    consent_service, store, consent, record = stored_evidence(prefix, b"parallel revoke")

    def revoke_once():
        return SpeechEvidenceRevocationService(
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

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _index: revoke_once(), range(2)))

    assert len({item.impact_digest for item in results}) == 1
    assert all(item.key_destroyed for item in results)
    with Session(concurrent_engine) as session:
        tombstones = session.exec(
            __import__("sqlmodel")
            .select(SpeechEvidenceRevocationDB)
            .where(SpeechEvidenceRevocationDB.evidence_digest == record.content_digest)
        ).all()
    assert len(tombstones) == 1


def test_remote_request_rejects_unsigned_and_mismatched_ack() -> None:
    prefix = "revocation-remote-invalid"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"remote invalid")
    service = SpeechEvidenceRevocationService(training=_NoopTrainingFence())
    service.revoke(
        principal(prefix),
        record.evidence_id,
        expected_consent_version=consent.consent_version,
        reason_code="speech_contributor_withdrawal",
        contributor_id=consent.speaker_id,
    )
    request_digest = digest(f"request-{prefix}")
    with pytest.raises(SpeechEvidenceRevocationError, match="remote_signature_invalid"):
        service.stage_remote_request(
            principal(prefix),
            evidence_digest=record.content_digest,
            request_digest=request_digest,
            signature_verified=False,
        )
    service.stage_remote_request(
        principal(prefix),
        evidence_digest=record.content_digest,
        request_digest=request_digest,
        signature_verified=True,
    )
    with pytest.raises(SpeechEvidenceRevocationError, match="remote_request_mismatch"):
        service.acknowledge_remote(
            principal(prefix),
            evidence_digest=record.content_digest,
            request_digest=digest("foreign-request"),
            ack_digest=digest("foreign-ack"),
            signature_verified=True,
        )
