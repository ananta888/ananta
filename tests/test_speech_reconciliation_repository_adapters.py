from __future__ import annotations

import time

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.repositories.speech_reconciliation import (
    SpeechReconciliationJobCreate,
    SpeechReconciliationRepository,
    SpeechReconciliationRepositoryError,
)
from agent.services.background.speech_reconciliation_reconciler import (
    SpeechReconciliationRecoveryReconciler,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_reconciliation_budget_service import (
    AdmittedSourceDuration,
    SpeechReconciliationBudgetService,
)
from agent.services.speech_reconciliation_job_service import job_contract
from agent.services.speech_reconciliation_repository_adapters import (
    RepositorySpeechReconciliationLeasePort,
)
from agent.services.speech_reconciliation_scheduler import (
    QueuedSpeechReconciliation,
    SpeechReconciliationWorkerCandidate,
)
from ananta_contracts.speech_reconciliation import SpeechResourceVector
from tests.speech_reconciliation_support import checkpoint_contract, digest


def _setup(label: str):
    now = time.time_ns() // 1_000_000
    tenant = f"tenant-adapter-{label}"
    owner = f"owner-adapter-{label}"
    consent_id = f"speech-consent-adapter-{label}"
    with Session(engine) as session:
        session.add(
            SpeechEvidenceConsentDB(
                id=consent_id,
                tenant_id=tenant,
                owner_subject=owner,
                speaker_id=f"speaker-{label}",
                recipient_id=f"recipient-{label}",
                pair_id=f"pair-{label}",
                session_id=f"session-{label}",
                session_epoch=1,
                direction="sender_to_receiver",
                purpose="speech_reconciliation",
                scope_digest=digest(f"scope-{label}"),
                consent_digest=digest(f"consent-{label}"),
                state="active",
                issued_at_ms=now - 1000,
                expires_at_ms=now + 300_000,
            )
        )
        session.commit()
    plan = SpeechReconciliationBudgetService().plan(
        [AdmittedSourceDuration(digest(f"source-{label}"), 60_000)], compute_factor=2
    )
    spec = SpeechReconciliationJobCreate(
        job_id=f"speech-reconciliation-adapter-{label}",
        tenant_id=tenant,
        owner_subject=owner,
        pair_scope_digest=digest(f"scope-{label}"),
        idempotency_key_digest=digest(f"idempotency-{label}"),
        request_digest=digest(f"request-{label}"),
        consent_id=consent_id,
        consent_version=1,
        revocation_epoch=0,
        input_manifest_digest=digest(f"manifest-{label}"),
        input_lineage_digest=digest(f"lineage-{label}"),
        input_artifact_ref=f"artifact://speech-evidence/{label}/input.enc",
        policy_digest=digest(f"policy-{label}"),
        research_policy_ref=None,
        budget_plan={
            "compute_factor": plan.compute_factor,
            "compute_equivalent_ms": plan.compute_equivalent_ms,
            "allocated": plan.total.to_dict(),
            "stages": {stage: vector.to_dict() for stage, vector in plan.stages.items()},
        },
        source_duration_ms=plan.source_duration_ms,
        max_compute_factor=2,
        key_epoch=1,
        deadline_at_ms=now + 240_000,
    )
    repository = SpeechReconciliationRepository()
    record, _ = repository.create_job(spec, now_ms=now)
    return now, spec, repository, record


def _queued(spec, record, *, checkpoint_ref=None):
    resources = SpeechResourceVector(wall_time_ms=100, cpu_time_ms=100, disk_bytes=100)
    return QueuedSpeechReconciliation(
        job=job_contract(record),
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        priority=10,
        queued_sequence=1,
        allowed_locations=frozenset({"local"}),
        requested_resources=resources,
        checkpoint_ref=checkpoint_ref,
    )


def _worker():
    return SpeechReconciliationWorkerCandidate(
        worker_id="reconciliation-worker-local",
        location="local",
        capabilities=frozenset({"speech_reconciliation"}),
        capacity=SpeechResourceVector(
            wall_time_ms=1000,
            cpu_time_ms=1000,
            memory_byte_ms=1000,
            disk_bytes=1000,
            checkpoint_bytes=1000,
        ),
        max_offline_assignments=1,
        active_offline_assignments=0,
    )


def test_repository_lease_adapter_mints_one_fenced_attempt_and_revoke_requeues() -> None:
    now, spec, repository, record = _setup("lease")
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: now + 10),
        secret=b"speech-reconciliation-lease-audit" * 2,
    )
    leases = RepositorySpeechReconciliationLeasePort(
        repository,
        clock_ms=lambda: now + 10,
        audit=audit,
    )
    lease = leases.acquire(_queued(spec, record), _worker(), ttl_ms=30_000)
    assert lease.job.attempt_id != "speech-reconciliation-unclaimed"
    assert lease.job.fencing_epoch == 1
    with pytest.raises(SpeechReconciliationRepositoryError, match="attempt_already_claimed"):
        leases.acquire(_queued(spec, record), _worker(), ttl_ms=30_000)
    leases.revoke(lease.lease_id, reason_code="speech_reconciliation_projection_failed")
    current = repository.get_job(tenant_id=spec.tenant_id, owner_subject=spec.owner_subject, job_id=record.id)
    assert current is not None and current.state == "queued" and current.active_attempt_id is None
    rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", spec.tenant_id),
        scope_digest=audit.digest("scope", f"speech-job:{record.id}"),
        after_event_id=None,
        limit=10,
        now_ms=now + 10,
    )
    assert {row.event_type for row in rows} == {"semantic_budget", "semantic_lease", "semantic_job"}
    assert [row.transition for row in rows if row.event_type == "semantic_lease"] == ["acquired", "fenced"]
    assert [row.transition for row in rows if row.event_type == "semantic_job"] == ["running", "queued"]
    assert any(row.lease_ref and row.job_ref for row in rows)


def test_recovery_uses_last_bound_checkpoint_and_old_fence_cannot_reactivate() -> None:
    now, spec, repository, record = _setup("recovery-port")
    leases = RepositorySpeechReconciliationLeasePort(repository, clock_ms=lambda: now + 10)
    first = leases.acquire(_queued(spec, record), _worker(), ttl_ms=5_000)
    checkpoint = checkpoint_contract(first.job)
    persisted = repository.save_checkpoint(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=first.job,
        checkpoint=checkpoint,
        now_ms=now + 20,
    )
    candidates = repository.list_recovery_candidates(now_ms=now + 5_011, limit=10)
    assert len(candidates) == 1 and candidates[0].checkpoint_ref == checkpoint.checkpoint_ref
    summary = SpeechReconciliationRecoveryReconciler(
        repository,
        clock_ms=lambda: now + 5_011,
    ).run_once()
    assert summary.retried == 1 and summary.conflicts == 0
    latest = repository.latest_checkpoint_ref(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=record.id,
    )
    assert latest == (checkpoint.checkpoint_ref, checkpoint.checkpoint_digest, 1)
    with pytest.raises(SpeechReconciliationRepositoryError, match="speech_reconciliation_fence_stale"):
        repository.heartbeat(
            job_id=record.id,
            attempt_id=first.job.attempt_id,
            fencing_epoch=first.job.fencing_epoch,
            fencing_token_digest=first.job.fencing_token_digest,
            expected_version=persisted.version,
            lease_expires_at_ms=now + 20_000,
            now_ms=now + 5_012,
        )
    current = repository.get_job(tenant_id=spec.tenant_id, owner_subject=spec.owner_subject, job_id=record.id)
    assert current is not None
    second = RepositorySpeechReconciliationLeasePort(repository, clock_ms=lambda: now + 5_020).acquire(
        _queued(spec, current, checkpoint_ref=checkpoint.checkpoint_ref),
        _worker(),
        ttl_ms=5_000,
    )
    assert second.job.fencing_epoch == 2 and second.job.attempt_id != first.job.attempt_id
