from __future__ import annotations

import hashlib
import time

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.ml_intern_training import MlInternSpeechAdapterDB, MlInternTrainingJobDB
from agent.db_models.speech_evidence import (
    SpeechCurationTaskDB,
    SpeechDatasetManifestDB,
    SpeechPrivacyLifecycleDB,
)
from agent.db_models.speech_evidence_sync import SpeechEvidenceOfferDB
from agent.db_models.speech_reconciliation import SpeechReconciliationJobDB
from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
from agent.services.speech_evidence_revocation_service import SpeechEvidenceRevocationService
from agent.services.speech_privacy_lifecycle_service import (
    SAFE_STATE_BY_PHASE,
    SPEECH_DATA_PHASES,
    SpeechPrivacyLifecycleError,
)
from agent.services.speech_privacy_production_composition import (
    PRODUCTION_SPEECH_PRIVACY_PHASES,
    ProductionSpeechPrivacyFencePort,
    SqlSpeechPrivacyBindingResolver,
    SqlSpeechPrivacyPhaseRepository,
    SqlSpeechPrivacyTombstoneRepository,
    build_speech_privacy_lifecycle_service,
)
from tests.speech_evidence_support import digest, principal, stored_evidence


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _publish_phase_lineage(prefix: str, *, phase: str, evidence_digest: str, consent_id: str) -> str | None:
    kind = {
        "dataset": "manifest",
        "reconciliation": "reconciliation",
        "training": "job",
        "evaluation": "evaluation",
        "approval": "adapter",
        "inference": "adapter",
    }.get(phase)
    nodes = [SpeechLineageNode("evidence", evidence_digest, consent_id=consent_id)]
    edges: list[SpeechLineageEdge] = []
    phase_digest = digest(f"{prefix}:{phase}:projection") if kind is not None else None
    parent_kind = "evidence"
    parent_digest = evidence_digest
    if kind is not None and phase_digest is not None:
        nodes.append(SpeechLineageNode(kind, phase_digest, consent_id=consent_id))
        edges.append(SpeechLineageEdge("evidence", evidence_digest, kind, phase_digest, "derived_from"))
        parent_kind = kind
        parent_digest = phase_digest
    export_digest = digest(f"{prefix}:{phase}:export")
    nodes.append(SpeechLineageNode("export", export_digest, consent_id=consent_id))
    edges.append(SpeechLineageEdge(parent_kind, parent_digest, "export", export_digest, "exported_as"))
    get_ml_intern_speech_lineage_service().publish(principal(prefix), nodes=tuple(nodes), edges=tuple(edges))
    return phase_digest


def _seed_phase_projection(prefix: str, *, phase: str, consent, record, phase_digest: str | None) -> None:
    now = time.time_ns() // 1_000_000
    with Session(engine) as session:
        if phase == "curation":
            session.add(
                SpeechCurationTaskDB(
                    id=f"speech-curation-{prefix}",
                    tenant_id=principal(prefix).tenant_id,
                    owner_subject=principal(prefix).subject,
                    parent_task_id=f"parent-{prefix}",
                    admission_digest=digest(f"{prefix}:admission"),
                    evidence_refs=[record.content_digest],
                    consent_id=consent.consent_id,
                    consent_version=consent.consent_version,
                    revocation_epoch=consent.revocation_epoch,
                    task_binding={"binding_digest": digest(f"{prefix}:task-binding")},
                    state="running",
                    deadline_epoch_ms=now + 60_000,
                    created_at_ms=now,
                    updated_at_ms=now,
                )
            )
        elif phase == "transfer":
            session.add(
                SpeechEvidenceOfferDB(
                    offer_id=f"offer-{prefix}",
                    tenant_id=principal(prefix).tenant_id,
                    proposal_verification_digest=digest(f"{prefix}:proposal"),
                    acceptance_verification_digest=digest(f"{prefix}:acceptance"),
                    session_id=record.session_id,
                    pair_id=record.pair_id,
                    epoch=record.session_epoch,
                    sender_id=consent.speaker_id,
                    recipient_id=consent.recipient_id,
                    inventory_root_digest=digest(f"{prefix}:inventory"),
                    direction=consent.direction,
                    purpose=consent.purpose,
                    data_classes=["transcript"],
                    fields=["transcript"],
                    retention_seconds=600,
                    trainer_class="trainer-local",
                    group_ids=[digest(f"{prefix}:group")],
                    total_bytes=128,
                    sender_consent_digest=consent.consent_digest,
                    recipient_consent_digest=consent.consent_digest,
                    scope_digest=consent.scope_digest,
                    expires_at_ms=now + 60_000,
                    state="accepted",
                    transfer_started=True,
                    created_at_ms=now,
                    updated_at_ms=now,
                )
            )
        elif phase == "dataset":
            assert phase_digest is not None
            session.add(
                SpeechDatasetManifestDB(
                    tenant_id=principal(prefix).tenant_id,
                    owner_subject=principal(prefix).subject,
                    dataset_id=f"dataset-{prefix}",
                    version="v1",
                    manifest_digest=phase_digest,
                    manifest_payload={"schema": "ananta.speech-dataset-manifest.v1"},
                    record_count=1,
                    consent_refs=[consent.consent_id],
                    revocation_epoch=consent.revocation_epoch,
                    status="active",
                    created_at_ms=now,
                )
            )
        elif phase == "reconciliation":
            assert phase_digest is not None
            session.add(
                SpeechReconciliationJobDB(
                    id=f"speech-reconciliation-{prefix}",
                    tenant_id=principal(prefix).tenant_id,
                    owner_subject=principal(prefix).subject,
                    pair_scope_digest=consent.scope_digest,
                    idempotency_key_digest=digest(f"{prefix}:reconciliation:idempotency"),
                    request_digest=phase_digest,
                    state="running",
                    stage="correction",
                    reason_code="speech_reconciliation_running",
                    consent_id=consent.consent_id,
                    consent_version=consent.consent_version,
                    revocation_epoch=consent.revocation_epoch,
                    input_manifest_digest=digest(f"{prefix}:input-manifest"),
                    input_lineage_digest=digest(f"{prefix}:input-lineage"),
                    input_artifact_ref=f"artifact://speech-reconciliation/{prefix}",
                    policy_digest=digest(f"{prefix}:policy"),
                    budget_plan={"max_compute_factor": 1},
                    source_duration_ms=1_000,
                    max_compute_factor=1,
                    key_epoch=1,
                    deadline_at_ms=now + 60_000,
                    created_at_ms=now,
                    updated_at_ms=now,
                )
            )
        elif phase == "training":
            assert phase_digest is not None
            session.add(
                MlInternTrainingJobDB(
                    tenant_id=principal(prefix).tenant_id,
                    owner_subject=principal(prefix).subject,
                    task_id=f"task-{prefix}",
                    idempotency_key_digest=digest(f"{prefix}:training:idempotency"),
                    request_digest=phase_digest,
                    status="running",
                )
            )
        elif phase in {"approval", "inference"}:
            assert phase_digest is not None
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
                    base_model_digest=digest(f"{prefix}:base-model"),
                    backend="test-backend",
                    backend_digest=digest(f"{prefix}:backend"),
                    dataset_digest=digest(f"{prefix}:dataset"),
                    split_digest=digest(f"{prefix}:split"),
                    evaluation_report_digest=digest(f"{prefix}:evaluation"),
                    consent_digest=consent.consent_digest,
                    consent_expires_at_ms=consent.expires_at_ms,
                    artifact_ref=f"artifact://speech-adapters/{prefix}",
                    artifact_sha256=phase_digest,
                    artifact_size_bytes=128,
                    expires_at_ms=consent.expires_at_ms,
                    status="approved",
                )
            )
        session.commit()


@pytest.mark.parametrize("phase", sorted(SPEECH_DATA_PHASES))
def test_production_revocation_reaches_every_phase_safe_state(phase: str) -> None:
    prefix = f"privacy-phase-{phase}"
    _consent_service, _store, consent, record = stored_evidence(
        prefix,
        f"private payload canary {phase}".encode(),
    )
    phase_digest = _publish_phase_lineage(
        prefix,
        phase=phase,
        evidence_digest=record.content_digest,
        consent_id=consent.consent_id,
    )
    _seed_phase_projection(prefix, phase=phase, consent=consent, record=record, phase_digest=phase_digest)

    service = build_speech_privacy_lifecycle_service(principal(prefix))
    row, created = service.revoke(
        scope_digest=consent.scope_digest,
        evidence_digest=record.content_digest,
        phase=phase,
        revocation_epoch=1,
        remote_required=True,
    )

    assert PRODUCTION_SPEECH_PRIVACY_PHASES == SPEECH_DATA_PHASES
    assert created and row.local_fenced and row.key_destroyed
    assert row.safe_state == SAFE_STATE_BY_PHASE[phase]
    assert row.remote_state == "unresolved"
    replay, replay_created = build_speech_privacy_lifecycle_service(principal(prefix)).revoke(
        scope_digest=consent.scope_digest,
        evidence_digest=record.content_digest,
        phase=phase,
        revocation_epoch=1,
        remote_required=True,
    )
    assert replay == row and not replay_created
    with pytest.raises(SpeechPrivacyLifecycleError, match="speech_privacy_reimport_revoked"):
        service.preflight_import(evidence_digest=record.content_digest)


def test_persistent_restart_late_signed_ack_and_content_free_tombstone() -> None:
    prefix = "privacy-restart-ack"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"restart private transcript canary")
    _publish_phase_lineage(
        prefix,
        phase="quarantine",
        evidence_digest=record.content_digest,
        consent_id=consent.consent_id,
    )
    first = build_speech_privacy_lifecycle_service(principal(prefix))
    row, _ = first.revoke(
        scope_digest=consent.scope_digest,
        evidence_digest=record.content_digest,
        phase="quarantine",
        revocation_epoch=1,
        remote_required=True,
    )

    restarted = build_speech_privacy_lifecycle_service(principal(prefix))
    with pytest.raises(SpeechPrivacyLifecycleError, match="speech_privacy_remote_ack_invalid"):
        restarted.acknowledge_remote(
            evidence_digest=record.content_digest,
            request_digest=_digest("wrong"),
            ack_digest=_digest("ack"),
            signature_verified=True,
        )
    acknowledged = restarted.acknowledge_remote(
        evidence_digest=record.content_digest,
        request_digest=row.remote_request_digest or "",
        ack_digest=_digest("ack"),
        signature_verified=True,
    )
    assert acknowledged.remote_state == "acknowledged"
    assert (
        restarted.acknowledge_remote(
            evidence_digest=record.content_digest,
            request_digest=row.remote_request_digest or "",
            ack_digest=_digest("ack"),
            signature_verified=True,
        )
        == acknowledged
    )
    with pytest.raises(SpeechPrivacyLifecycleError, match="speech_privacy_tombstone_conflict"):
        restarted.revoke(
            scope_digest=consent.scope_digest,
            evidence_digest=record.content_digest,
            phase="quarantine",
            revocation_epoch=1,
            remote_required=False,
        )
    with Session(engine) as session:
        persisted = session.exec(
            select(SpeechPrivacyLifecycleDB).where(
                SpeechPrivacyLifecycleDB.evidence_digest == record.content_digest
            )
        ).one()
    projection = persisted.model_dump()
    assert projection["remote_state"] == "acknowledged"
    assert "restart private transcript canary" not in repr(projection)
    assert all(isinstance(projection[name], str) and len(projection[name]) == 64 for name in (
        "scope_digest",
        "evidence_digest",
        "remote_request_digest",
        "remote_ack_digest",
    ))


def test_restart_resumes_reserved_but_not_yet_completed_product_fence() -> None:
    prefix = "privacy-crash-resume"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"crash window canary")
    _publish_phase_lineage(
        prefix,
        phase="quarantine",
        evidence_digest=record.content_digest,
        consent_id=consent.consent_id,
    )
    resolver = SqlSpeechPrivacyBindingResolver(principal(prefix))
    tombstones = SqlSpeechPrivacyTombstoneRepository(principal(prefix))
    fence = ProductionSpeechPrivacyFencePort(
        principal(prefix),
        resolver=resolver,
        phases=SqlSpeechPrivacyPhaseRepository(principal(prefix)),
        revocations=SpeechEvidenceRevocationService(),
        tombstones=tombstones,
    )

    assert fence.fence(
        scope_digest=consent.scope_digest,
        evidence_digest=record.content_digest,
        phase="quarantine",
        revocation_epoch=1,
    )
    assert tombstones.get(record.content_digest) is None

    completed, created = build_speech_privacy_lifecycle_service(principal(prefix)).revoke(
        scope_digest=consent.scope_digest,
        evidence_digest=record.content_digest,
        phase="quarantine",
        revocation_epoch=1,
        remote_required=True,
    )
    assert created and completed.local_fenced and completed.key_destroyed
