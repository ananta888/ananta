from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import (
    MlInternDatasetDB,
    MlInternSpeechAdapterDB,
    MlInternSpeechAdapterLegacyImportDB,
    MlInternTrainingAttemptDB,
    MlInternTrainingCapacityLeaseDB,
    MlInternTrainingEventDB,
    MlInternTrainingExecutionLeaseDB,
    MlInternTrainingJobDB,
    SemanticMediaAuditEventDB,
    SemanticMediaAuditOutboxDB,
)
from agent.repositories.ml_intern_speech_adapter_registry import MlInternSpeechAdapterRepository
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepository,
    MlInternTrainingRepositoryConflict,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.ml_intern_speech_adapter_registry import MlInternSpeechAdapterRegistry
from agent.services.ml_intern_speech_eval_service import MlInternSpeechEvalService
from agent.services.ml_intern_speech_revocation_service import (
    SqlSpeechAdapterFencePort,
    SqlSpeechTrainingFencePort,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import speech_scope_digest
from tests.speech_adaptation_support import digest, speech_job
from worker.speech_training.evaluation import build_mock_evaluation


class _NoLineage:
    def publish_registration(self, record) -> None:
        del record

    def publish_export(self, record, receipt, *, export_consent_digest: str) -> None:
        del record, receipt, export_consent_digest


@pytest.fixture
def atomic_engine():
    configured = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(configured)
    return configured


def _audit() -> SemanticMediaAuditRecorder:
    return SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: 1_000_000,
        ),
        secret=b"ml-intern-atomic-audit-test-key" * 2,
    )


def _evaluation():
    job = speech_job()
    report = build_mock_evaluation(job)
    report["hardware_profile"] = "synthetic-openvoice-v2-contract-test"
    return MlInternSpeechEvalService().decide(report, expected_bindings=report["bindings"])


def _register(registry: MlInternSpeechAdapterRegistry, adapter_id: str):
    pair_id = "pair-atomic"
    direction = "sender_to_receiver"
    speaker = digest("speaker-atomic")
    return registry.register_evaluated(
        adapter_id=adapter_id,
        version="v1",
        tenant_id="tenant-atomic",
        owner_subject="owner-atomic",
        pair_id=pair_id,
        direction=direction,
        speaker_digest=speaker,
        scope_digest=speech_scope_digest(
            pair_id=pair_id,
            direction=direction,
            speaker_digest=speaker,
        ),
        base_model_id="openvoice-v2-test",
        base_model_digest=digest("model-atomic"),
        backend="mock",
        backend_digest=digest("backend-atomic"),
        dataset_digest=digest("dataset-atomic"),
        split_digest=digest("split-atomic"),
        evaluation=_evaluation(),
        consent_digest=digest("consent-atomic"),
        consent_expires_at_ms=1_200_000,
        artifact_ref=f"artifact://speech-adapters/atomic/{adapter_id}",
        artifact_sha256=digest(adapter_id),
        artifact_size_bytes=128,
        expires_at_ms=1_100_000,
    )


def _registry(atomic_engine, path, *, audit=None) -> MlInternSpeechAdapterRegistry:
    return MlInternSpeechAdapterRegistry(
        path,
        clock_ms=lambda: 1_000_000,
        export_lineage=_NoLineage(),
        authority_audit=audit,
        repository=MlInternSpeechAdapterRepository(db_engine=atomic_engine),
    )


def test_adapter_sql_is_sole_authority_and_replay_is_exactly_once(atomic_engine, tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    registry = _registry(atomic_engine, registry_path, audit=_audit())
    created = _register(registry, "adapter-atomic")
    approved = registry.approve(
        created.adapter_id,
        tenant_id=created.tenant_id,
        owner_subject=created.owner_subject,
        pair_id=created.pair_id,
        direction=created.direction,
        expected_version=1,
        authorized_confirmation=True,
        approved_by="hub-admin",
        reason_code="manual_quality_approval",
        current_consent_digest=created.consent_digest,
    )
    replay = registry.approve(
        created.adapter_id,
        tenant_id=created.tenant_id,
        owner_subject=created.owner_subject,
        pair_id=created.pair_id,
        direction=created.direction,
        expected_version=1,
        authorized_confirmation=True,
        approved_by="hub-admin",
        reason_code="manual_quality_approval",
        current_consent_digest=created.consent_digest,
    )

    assert replay.to_dict() == approved.to_dict()
    assert not registry_path.exists()
    with Session(atomic_engine) as db:
        row = db.get(MlInternSpeechAdapterDB, created.adapter_id)
        pending = db.exec(select(SemanticMediaAuditOutboxDB)).all()
    assert row is not None and row.status == "approved" and row.registry_version == 2
    assert len(pending) == 2
    assert {item.transition for item in pending} == {"evaluated", "approved"}

    delivered = SqlSemanticMediaAuditOutbox(
        db_engine=atomic_engine,
        clock_ms=lambda: 2_000_000,
    ).dispatch_pending(limit=20)
    assert (delivered.delivered, delivered.replayed, delivered.pending) == (2, 0, 0)
    with Session(atomic_engine) as db:
        assert len(db.exec(select(SemanticMediaAuditEventDB)).all()) == 2


def test_adapter_audit_enqueue_failure_rolls_back_cas_mutation(
    atomic_engine,
    tmp_path,
    monkeypatch,
) -> None:
    registry = _registry(atomic_engine, tmp_path / "registry.json", audit=_audit())
    created = _register(registry, "adapter-fault")
    original = SqlSemanticMediaAuditOutbox.enqueue_in_session

    def fail_after_staging(db: Session, event) -> bool:
        original(db, event)
        raise RuntimeError("injected adapter audit fault")

    monkeypatch.setattr(
        SqlSemanticMediaAuditOutbox,
        "enqueue_in_session",
        staticmethod(fail_after_staging),
    )
    with pytest.raises(RuntimeError, match="injected adapter audit fault"):
        registry.approve(
            created.adapter_id,
            tenant_id=created.tenant_id,
            owner_subject=created.owner_subject,
            pair_id=created.pair_id,
            direction=created.direction,
            expected_version=1,
            authorized_confirmation=True,
            approved_by="hub-admin",
            reason_code="manual_quality_approval",
            current_consent_digest=created.consent_digest,
        )

    with Session(atomic_engine) as db:
        row = db.get(MlInternSpeechAdapterDB, created.adapter_id)
        pending = db.exec(select(SemanticMediaAuditOutboxDB)).all()
    assert row is not None and row.status == "evaluated" and row.registry_version == 1
    assert len(pending) == 1 and pending[0].transition == "evaluated"


def test_legacy_adapter_json_is_imported_once_and_never_updated(atomic_engine, tmp_path) -> None:
    source_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(source_engine)
    source_registry = _registry(source_engine, tmp_path / "absent.json")
    legacy_record = _register(source_registry, "adapter-legacy")
    legacy_path = tmp_path / "legacy-registry.json"
    original_bytes = json.dumps(
        {
            "schema": "ananta.speech-adapter-registry.v1",
            "adapters": [legacy_record.to_dict()],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    legacy_path.write_bytes(original_bytes)

    first = _registry(atomic_engine, legacy_path, audit=_audit())
    second = _registry(atomic_engine, legacy_path, audit=_audit())
    imported = second.get_for_pair(
        legacy_record.adapter_id,
        tenant_id=legacy_record.tenant_id,
        owner_subject=legacy_record.owner_subject,
        pair_id=legacy_record.pair_id,
        direction=legacy_record.direction,
    )
    assert imported.to_dict() == legacy_record.to_dict()
    assert first.list_for_pair(
        tenant_id=legacy_record.tenant_id,
        owner_subject=legacy_record.owner_subject,
        pair_id=legacy_record.pair_id,
        direction=legacy_record.direction,
    ) == [imported]
    assert legacy_path.read_bytes() == original_bytes
    with Session(atomic_engine) as db:
        assert len(db.exec(select(MlInternSpeechAdapterLegacyImportDB)).all()) == 1
        pending = db.exec(select(SemanticMediaAuditOutboxDB)).all()
    assert len(pending) == 1 and pending[0].transition == "legacy_imported"


def _principal() -> MlInternTrainingPrincipal:
    return MlInternTrainingPrincipal("tenant-training-atomic", "owner-training-atomic")


def _dataset(principal: MlInternTrainingPrincipal, name: str) -> MlInternDatasetDB:
    return MlInternDatasetDB(
        id=f"dataset-{name}",
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        name=f"{name}.jsonl",
        content_sha256=hashlib.sha256(name.encode()).hexdigest(),
        storage_ref=f"artifact://ml-intern-datasets/{name}",
        size_bytes=128,
        record_count=2,
    )


def _job(principal: MlInternTrainingPrincipal, dataset_id: str) -> MlInternTrainingJobDB:
    return MlInternTrainingJobDB(
        id=f"job-{uuid.uuid4()}",
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        task_id=f"task-{uuid.uuid4()}",
        dataset_id=dataset_id,
        idempotency_key_digest=digest(f"idempotency-{uuid.uuid4()}"),
        request_digest=digest(f"request-{uuid.uuid4()}"),
    )


def test_all_training_authorities_enqueue_closed_exactly_once_audit(atomic_engine) -> None:
    audit = _audit()
    repository = MlInternTrainingRepository(db_engine=atomic_engine, audit=audit, clock=lambda: 1000.0)
    principal = _principal()
    dataset, replayed = repository.create_dataset(_dataset(principal, "primary"))
    duplicate, duplicate_replayed = repository.create_dataset(_dataset(principal, "primary"))
    assert replayed is False and duplicate_replayed is True and duplicate.id == dataset.id
    dataset.status = "validated"
    repository.save_dataset(dataset, expected_version=1)

    disposable, _ = repository.create_dataset(_dataset(principal, "disposable"))
    assert repository.delete_dataset(principal, disposable.id) is True

    job, job_replayed = repository.create_job_with_capacity(_job(principal, dataset.id), outstanding_limit=2)
    replay_job = MlInternTrainingJobDB.model_validate(job.model_dump())
    replayed_job, duplicate_job = repository.create_job_with_capacity(replay_job, outstanding_limit=2)
    assert job_replayed is False and duplicate_job is True and replayed_job.id == job.id

    event = repository.append_event(
        principal,
        job.id,
        event_type="queued",
        dedupe_key="queued-once",
        payload={"status": "queued"},
    )
    assert repository.append_event(
        principal,
        job.id,
        event_type="queued",
        dedupe_key="queued-once",
        payload={"status": "queued"},
    ).id == event.id

    assert repository.try_acquire_execution_slot(
        job.id,
        limit=1,
        lease_expires_at=2000.0,
        now=1000.0,
    ) == 0
    assert repository.renew_execution_slot(job.id, lease_expires_at=3000.0) is True
    repository.release_execution_slot(job.id)
    # Reusing numeric slot zero is a new authority generation, while retrying
    # one generation must remain idempotent.
    assert repository.try_acquire_execution_slot(
        job.id,
        limit=1,
        lease_expires_at=4000.0,
        now=1000.0,
    ) == 0
    repository.release_execution_slot(job.id)

    attempt = repository.create_attempt(
        MlInternTrainingAttemptDB(
            id="attempt-atomic",
            job_id=job.id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            attempt_number=1,
            worker_id="worker-atomic",
            worker_url="internal://worker-atomic",
            fencing_token_digest=digest("fencing-atomic"),
            lease_expires_at=2000.0,
            deadline_at=3000.0,
        )
    )
    assert repository.create_attempt(MlInternTrainingAttemptDB.model_validate(attempt.model_dump())).id == attempt.id
    attempt.status = "completed"
    repository.save_attempt(attempt, expected_version=1)
    job.status = "completed"
    job.phase = "completed"
    repository.save_job(job, expected_version=1)
    with pytest.raises(MlInternTrainingRepositoryConflict, match="job_version_conflict"):
        repository.save_job(job, expected_version=1)

    with Session(atomic_engine) as db:
        pending = db.exec(select(SemanticMediaAuditOutboxDB)).all()
        assert db.exec(select(MlInternTrainingCapacityLeaseDB)).all() == []
        assert db.exec(select(MlInternTrainingExecutionLeaseDB)).all() == []
        assert len(db.exec(select(MlInternTrainingEventDB)).all()) == 1
    assert len(pending) == 16
    assert len({row.idempotency_digest for row in pending}) == 16
    assert {row.event_type for row in pending} == {"speech_dataset", "speech_training"}
    projection = {
        column.name: getattr(pending[0], column.name)
        for column in SemanticMediaAuditOutboxDB.__table__.columns
    }
    assert not {"payload", "audio", "transcript", "tenant_id", "owner_subject"}.intersection(projection)
    assert principal.tenant_id not in repr(projection)
    assert principal.subject not in repr(projection)

    result = SqlSemanticMediaAuditOutbox(
        db_engine=atomic_engine,
        clock_ms=lambda: 2_000_000,
    ).dispatch_pending(limit=100)
    assert (result.delivered, result.failed, result.pending) == (16, 0, 0)
    with Session(atomic_engine) as db:
        assert len(db.exec(select(SemanticMediaAuditEventDB)).all()) == 16


def test_training_domain_and_outbox_rollback_together_on_audit_fault(
    atomic_engine,
    monkeypatch,
) -> None:
    repository = MlInternTrainingRepository(db_engine=atomic_engine, audit=_audit())
    principal = _principal()
    original = SqlSemanticMediaAuditOutbox.enqueue_in_session

    def fail_after_staging(db: Session, event) -> bool:
        original(db, event)
        raise RuntimeError("injected training audit fault")

    monkeypatch.setattr(
        SqlSemanticMediaAuditOutbox,
        "enqueue_in_session",
        staticmethod(fail_after_staging),
    )
    job = _job(principal, "")
    job.dataset_id = None
    with pytest.raises(RuntimeError, match="injected training audit fault"):
        repository.create_job_with_capacity(job, outstanding_limit=1)

    with Session(atomic_engine) as db:
        assert db.exec(select(MlInternTrainingJobDB)).all() == []
        assert db.exec(select(MlInternTrainingCapacityLeaseDB)).all() == []
        assert db.exec(select(SemanticMediaAuditOutboxDB)).all() == []


def test_revocation_ports_delegate_to_single_atomic_sql_authorities(
    atomic_engine,
    tmp_path,
) -> None:
    audit = _audit()
    training = MlInternTrainingRepository(
        db_engine=atomic_engine,
        audit=audit,
        clock=lambda: 1000.0,
    )
    principal = _principal()
    lineage_digest = digest("revoked-training-lineage")
    job = _job(principal, "")
    job.dataset_id = None
    job.request_digest = lineage_digest
    created_job, _ = training.create_job_with_capacity(job, outstanding_limit=1)

    registry_path = tmp_path / "registry.json"
    registry = _registry(atomic_engine, registry_path, audit=audit)
    adapter = _register(registry, "adapter-revocation")
    voice_principal = VoicePrincipal(principal.tenant_id, principal.subject)

    jobs = SqlSpeechTrainingFencePort(training)
    adapters = SqlSpeechAdapterFencePort(registry)
    assert jobs.fence(
        voice_principal,
        lineage_digest=lineage_digest,
        revocation_epoch=7,
    ) is True
    assert adapters.fence(
        VoicePrincipal(adapter.tenant_id, adapter.owner_subject),
        lineage_digest=adapter.artifact_sha256,
        revocation_epoch=7,
    ) is True

    # Replays repair a missing audit command, but neither mutate authority a
    # second time nor add duplicate outbox rows.
    assert jobs.fence(
        voice_principal,
        lineage_digest=lineage_digest,
        revocation_epoch=7,
    ) is True
    assert adapters.fence(
        VoicePrincipal(adapter.tenant_id, adapter.owner_subject),
        lineage_digest=adapter.artifact_sha256,
        revocation_epoch=7,
    ) is True

    with Session(atomic_engine) as db:
        saved_job = db.get(MlInternTrainingJobDB, created_job.id)
        saved_adapter = db.get(MlInternSpeechAdapterDB, adapter.adapter_id)
        pending = db.exec(select(SemanticMediaAuditOutboxDB)).all()
        capacity = db.exec(select(MlInternTrainingCapacityLeaseDB)).all()
    assert saved_job is not None
    assert (saved_job.status, saved_job.error_code, saved_job.version) == (
        "cancelled",
        "speech_evidence_revoked",
        2,
    )
    assert saved_adapter is not None
    assert (saved_adapter.status, saved_adapter.registry_version) == ("revoked", 2)
    assert capacity == []
    assert not registry_path.exists()
    assert len(pending) == 6
    assert len({row.idempotency_digest for row in pending}) == 6
    assert {row.transition for row in pending} == {
        "job_created",
        "capacity_acquired",
        "job_fenced",
        "capacity_released",
        "evaluated",
        "revoked",
    }


def test_training_revocation_and_audit_roll_back_as_one_transaction(
    atomic_engine,
    monkeypatch,
) -> None:
    repository = MlInternTrainingRepository(db_engine=atomic_engine, audit=_audit())
    principal = _principal()
    lineage_digest = digest("revocation-rollback")
    job = _job(principal, "")
    job.dataset_id = None
    job.request_digest = lineage_digest
    created, _ = repository.create_job_with_capacity(job, outstanding_limit=1)
    original = SqlSemanticMediaAuditOutbox.enqueue_in_session

    def fail_after_staging(db: Session, event) -> bool:
        original(db, event)
        raise RuntimeError("injected revocation audit fault")

    monkeypatch.setattr(
        SqlSemanticMediaAuditOutbox,
        "enqueue_in_session",
        staticmethod(fail_after_staging),
    )
    with pytest.raises(RuntimeError, match="injected revocation audit fault"):
        repository.fence_by_request_digest(
            principal,
            request_digest=lineage_digest,
            revocation_epoch=2,
        )

    with Session(atomic_engine) as db:
        saved = db.get(MlInternTrainingJobDB, created.id)
        pending = db.exec(select(SemanticMediaAuditOutboxDB)).all()
        capacity = db.exec(select(MlInternTrainingCapacityLeaseDB)).all()
    assert saved is not None
    assert (saved.status, saved.error_code, saved.version) == ("queued", None, 1)
    assert len(capacity) == 1 and capacity[0].job_id == created.id
    assert len(pending) == 2
    assert {row.transition for row in pending} == {"job_created", "capacity_acquired"}
