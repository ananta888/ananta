from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    SemanticMediaAuditOutboxDB,
    SpeechReconciliationMutationDB,
)
from agent.repositories.speech_reconciliation import (
    SpeechReconciliationJobCreate,
    SpeechReconciliationRepository,
    SpeechReconciliationRepositoryError,
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
from tests.speech_reconciliation_support import (
    checkpoint_contract,
    digest,
    job_contract,
    result_contract,
)


def _spec(label: str = "repository") -> SpeechReconciliationJobCreate:
    budget = SpeechReconciliationBudgetService().plan(
        [AdmittedSourceDuration(digest(f"source-{label}"), 60_000)],
        compute_factor=10,
    )
    return SpeechReconciliationJobCreate(
        job_id=f"speech-reconciliation-{label}",
        tenant_id=f"tenant-{label}",
        owner_subject=f"owner-{label}",
        pair_scope_digest=digest(f"pair-{label}"),
        idempotency_key_digest=digest(f"idempotency-{label}"),
        request_digest=digest(f"request-{label}"),
        consent_id=f"speech-consent-{label}",
        consent_version=1,
        revocation_epoch=0,
        input_manifest_digest=digest(f"manifest-{label}"),
        input_lineage_digest=digest(f"lineage-{label}"),
        input_artifact_ref=f"artifact://speech-evidence/{label}/input.enc",
        policy_digest=digest(f"policy-{label}"),
        research_policy_ref=None,
        budget_plan={
            "compute_factor": budget.compute_factor,
            "compute_equivalent_ms": budget.compute_equivalent_ms,
            "allocated": budget.total.to_dict(),
            "stages": {stage: vector.to_dict() for stage, vector in budget.stages.items()},
        },
        source_duration_ms=60_000,
        max_compute_factor=10,
        key_epoch=1,
        deadline_at_ms=2_000_000,
    )


def test_idempotent_create_owner_isolation_and_factor_reduction() -> None:
    repo = SpeechReconciliationRepository()
    spec = _spec("create")
    created, fresh = repo.create_job(spec, now_ms=1_000_000)
    replay, replay_fresh = repo.create_job(spec, now_ms=1_000_001)
    assert fresh and not replay_fresh and replay == created
    assert repo.get_job(tenant_id="foreign", owner_subject=spec.owner_subject, job_id=spec.job_id) is None
    reduced_result = repo.reduce_factor(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=spec.job_id,
        max_compute_factor=5,
        expected_version=created.version,
        reason_code="user_reduced_factor",
        idempotency_key_digest=digest("reduce-create-key"),
        request_digest=digest("reduce-create-request"),
        now_ms=1_000_002,
    )
    reduced = reduced_result.job
    assert reduced.max_compute_factor == 5
    with pytest.raises(SpeechReconciliationRepositoryError, match="speech_reconciliation_factor_increase_forbidden"):
        repo.reduce_factor(
            tenant_id=spec.tenant_id,
            owner_subject=spec.owner_subject,
            job_id=spec.job_id,
            max_compute_factor=6,
            expected_version=reduced.version,
            reason_code="invalid_increase",
            idempotency_key_digest=digest("increase-create-key"),
            request_digest=digest("increase-create-request"),
            now_ms=1_000_003,
        )


def test_claim_heartbeat_checkpoint_completion_and_stale_fence() -> None:
    repo = SpeechReconciliationRepository()
    spec = _spec("lifecycle")
    job, _ = repo.create_job(spec, now_ms=1_000_000)
    attempt = repo.claim_attempt(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=spec.job_id,
        expected_job_version=job.version,
        worker_id_digest=digest("worker"),
        worker_capability_digest=digest("capability"),
        location_digest=digest("location"),
        resource_profile_digest=digest("resources"),
        fencing_token_digest=digest("fence-lifecycle"),
        lease_expires_at_ms=1_100_000,
        now_ms=1_000_010,
    )
    heartbeat = repo.heartbeat(
        job_id=job.id,
        attempt_id=attempt.id,
        fencing_epoch=attempt.fencing_epoch,
        fencing_token_digest=attempt.fencing_token_digest,
        expected_version=attempt.version,
        lease_expires_at_ms=1_120_000,
        now_ms=1_000_020,
    )
    contract = job_contract(
        job_id=job.id,
        attempt_id=attempt.id,
        fencing_epoch=attempt.fencing_epoch,
        fencing_token_digest=attempt.fencing_token_digest,
        consent_id=spec.consent_id,
        consent_version=spec.consent_version,
        revocation_epoch=spec.revocation_epoch,
        input_manifest_digest=spec.input_manifest_digest,
        input_lineage_digest=spec.input_lineage_digest,
        input_artifact_ref=spec.input_artifact_ref,
        policy_digest=spec.policy_digest,
        source_duration_ms=spec.source_duration_ms,
        max_compute_factor=spec.max_compute_factor,
        key_epoch=spec.key_epoch,
        deadline_at_ms=spec.deadline_at_ms,
    )
    checkpoint = checkpoint_contract(contract)
    persisted = repo.save_checkpoint(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=contract,
        checkpoint=checkpoint,
        now_ms=1_000_030,
    )
    assert persisted.checkpoint_sequence == 1
    result = result_contract(contract)
    completed = repo.complete(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=contract,
        result=result,
        publication_authorized=True,
        now_ms=1_000_040,
    )
    assert completed.state == "completed" and completed.resolved_count == 3
    with pytest.raises(SpeechReconciliationRepositoryError, match="speech_reconciliation_fence_stale"):
        repo.heartbeat(
            job_id=job.id,
            attempt_id=attempt.id,
            fencing_epoch=attempt.fencing_epoch,
            fencing_token_digest=attempt.fencing_token_digest,
            expected_version=heartbeat.version,
            lease_expires_at_ms=1_130_000,
            now_ms=1_000_050,
        )


def test_stale_attempt_is_fenced_and_job_requeued_for_bounded_retry() -> None:
    repo = SpeechReconciliationRepository()
    spec = _spec("recovery")
    job, _ = repo.create_job(spec, now_ms=1_000_000)
    attempt = repo.claim_attempt(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=spec.job_id,
        expected_job_version=job.version,
        worker_id_digest=digest("worker-recovery"),
        worker_capability_digest=digest("capability-recovery"),
        location_digest=digest("location-recovery"),
        resource_profile_digest=digest("resources-recovery"),
        fencing_token_digest=digest("fence-recovery"),
        lease_expires_at_ms=1_000_100,
        now_ms=1_000_010,
    )
    assert attempt.id in repo.expire_stale_attempts(now_ms=1_000_101)
    recovered = repo.get_job(tenant_id=spec.tenant_id, owner_subject=spec.owner_subject, job_id=job.id)
    assert recovered is not None and recovered.state == "queued" and recovered.active_attempt_id is None


def test_quality_extension_atomically_fences_attempt_requeues_and_stages_audit() -> None:
    label = "quality-extension"
    planned = SpeechReconciliationBudgetService().plan(
        [AdmittedSourceDuration(digest(f"source-{label}"), 60_000)],
        compute_factor=20,
    )
    original = _spec(label)
    spec = replace(
        original,
        max_compute_factor=20,
        current_compute_factor=10,
        budget_plan={
            "compute_factor": planned.compute_factor,
            "compute_equivalent_ms": planned.compute_equivalent_ms,
            "allocated": planned.total.to_dict(),
            "stages": {stage: vector.to_dict() for stage, vector in planned.stages.items()},
        },
    )
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(InMemorySemanticMediaAuditRepository(), clock_ms=lambda: 1_000_000),
        secret=b"speech-quality-extension-audit" * 2,
    )
    repo = SpeechReconciliationRepository(audit=audit)
    job, _ = repo.create_job(spec, now_ms=1_000_000)
    attempt = repo.claim_attempt(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
        expected_job_version=job.version,
        worker_id_digest=digest(f"worker-{label}"),
        worker_capability_digest=digest(f"capability-{label}"),
        location_digest=digest(f"location-{label}"),
        resource_profile_digest=digest(f"resources-{label}"),
        fencing_token_digest=digest(f"fence-{label}"),
        lease_expires_at_ms=1_100_000,
        now_ms=1_000_010,
    )
    contract = job_contract(
        job_id=job.id,
        attempt_id=attempt.id,
        fencing_epoch=attempt.fencing_epoch,
        fencing_token_digest=attempt.fencing_token_digest,
        consent_id=spec.consent_id,
        input_manifest_digest=spec.input_manifest_digest,
        input_lineage_digest=spec.input_lineage_digest,
        input_artifact_ref=spec.input_artifact_ref,
        policy_digest=spec.policy_digest,
        source_duration_ms=spec.source_duration_ms,
        max_compute_factor=20,
        deadline_at_ms=spec.deadline_at_ms,
    )
    repo.save_checkpoint(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=contract,
        checkpoint=checkpoint_contract(contract),
        now_ms=1_000_020,
    )
    extended = repo.apply_quality_decision(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=contract,
        action="extend",
        current_factor=10,
        next_factor=20,
        quality_score_micros=500_000,
        unresolved_count=1,
        unresolved_high_quality_conflicts=1,
        reason_code="speech_reconciliation_positive_trend",
        now_ms=1_000_030,
    )
    assert extended.state == "queued"
    assert extended.active_attempt_id is None
    assert extended.current_compute_factor == 20
    assert extended.quality_history[0]["attempt_id"] == attempt.id
    with Session(engine) as session:
        event = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.transition == "quality_extended",
                SemanticMediaAuditOutboxDB.reason_code == "speech_reconciliation_positive_trend",
            )
        ).first()
    assert event is not None
    replay = repo.apply_quality_decision(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=contract,
        action="extend",
        current_factor=10,
        next_factor=20,
        quality_score_micros=500_000,
        unresolved_count=1,
        unresolved_high_quality_conflicts=1,
        reason_code="speech_reconciliation_positive_trend",
        now_ms=1_000_040,
    )
    assert replay == extended
    second_attempt = repo.claim_attempt(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
        expected_job_version=extended.version,
        worker_id_digest=digest(f"worker-{label}-wave-2"),
        worker_capability_digest=digest(f"capability-{label}-wave-2"),
        location_digest=digest(f"location-{label}-wave-2"),
        resource_profile_digest=digest(f"resources-{label}-wave-2"),
        fencing_token_digest=digest(f"fence-{label}-wave-2"),
        lease_expires_at_ms=1_100_000,
        now_ms=1_000_050,
    )
    second_contract = job_contract(
        job_id=job.id,
        attempt_id=second_attempt.id,
        fencing_epoch=second_attempt.fencing_epoch,
        fencing_token_digest=second_attempt.fencing_token_digest,
        consent_id=spec.consent_id,
        input_manifest_digest=spec.input_manifest_digest,
        input_lineage_digest=spec.input_lineage_digest,
        input_artifact_ref=spec.input_artifact_ref,
        policy_digest=spec.policy_digest,
        source_duration_ms=spec.source_duration_ms,
        max_compute_factor=20,
        deadline_at_ms=spec.deadline_at_ms,
    )
    second_checkpoint = checkpoint_contract(
        second_contract,
        checkpoint_digest=digest(f"checkpoint-{label}-wave-2"),
        checkpoint_ref=f"artifact://speech-reconciliation-checkpoints/{label}/wave-2.enc",
        state_digest=digest(f"state-{label}-wave-2"),
    )
    persisted = repo.save_checkpoint(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=second_contract,
        checkpoint=second_checkpoint,
        now_ms=1_000_060,
    )
    assert persisted.checkpoint_sequence == 1
    latest = repo.latest_checkpoint_ref(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
    )
    assert latest == (
        second_checkpoint.checkpoint_ref,
        second_checkpoint.checkpoint_digest,
        second_checkpoint.checkpoint_sequence,
    )


def test_terminal_non_publication_result_is_allowed_but_cannot_bypass_fence() -> None:
    def claimed(label: str):
        repository = SpeechReconciliationRepository()
        specification = _spec(label)
        record, _ = repository.create_job(specification, now_ms=1_000_000)
        attempt = repository.claim_attempt(
            tenant_id=specification.tenant_id,
            owner_subject=specification.owner_subject,
            job_id=record.id,
            expected_job_version=record.version,
            worker_id_digest=digest(f"worker-{label}"),
            worker_capability_digest=digest(f"capability-{label}"),
            location_digest=digest(f"location-{label}"),
            resource_profile_digest=digest(f"resources-{label}"),
            fencing_token_digest=digest(f"fence-{label}"),
            lease_expires_at_ms=1_100_000,
            now_ms=1_000_010,
        )
        contract = job_contract(
            job_id=record.id,
            attempt_id=attempt.id,
            fencing_epoch=attempt.fencing_epoch,
            fencing_token_digest=attempt.fencing_token_digest,
            consent_id=specification.consent_id,
            input_manifest_digest=specification.input_manifest_digest,
            input_lineage_digest=specification.input_lineage_digest,
            input_artifact_ref=specification.input_artifact_ref,
            policy_digest=specification.policy_digest,
            source_duration_ms=specification.source_duration_ms,
            deadline_at_ms=specification.deadline_at_ms,
        )
        return repository, specification, contract

    repo, spec, contract = claimed("failed-no-publication")
    failed = result_contract(
        contract,
        status="failed",
        dataset_manifest_digest=None,
        dataset_artifact_ref=None,
        evaluation_digest=None,
        resolved_count=0,
        unresolved_count=0,
        rejected_count=0,
        reason_code="speech_reconciliation_asr_failed",
    )
    completed = repo.complete(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_contract=contract,
        result=failed,
        publication_authorized=False,
        now_ms=1_000_020,
    )
    assert completed.state == "failed"

    denied_repo, denied_spec, denied_contract = claimed("dataset-without-budget")
    with pytest.raises(SpeechReconciliationRepositoryError, match="budget_publication_denied"):
        denied_repo.complete(
            tenant_id=denied_spec.tenant_id,
            owner_subject=denied_spec.owner_subject,
            job_contract=denied_contract,
            result=result_contract(denied_contract),
            publication_authorized=False,
            now_ms=1_000_020,
        )

    stale_repo, stale_spec, stale_contract = claimed("stale-no-publication")
    running = stale_repo.get_job(
        tenant_id=stale_spec.tenant_id,
        owner_subject=stale_spec.owner_subject,
        job_id=stale_contract.job_id,
    )
    assert running is not None
    stale_repo.transition(
        tenant_id=stale_spec.tenant_id,
        owner_subject=stale_spec.owner_subject,
        job_id=stale_contract.job_id,
        target_state="paused",
        stage="slow_asr",
        reason_code="speech_reconciliation_user_paused",
        expected_version=running.version,
        idempotency_key_digest=digest("stale-pause-key"),
        request_digest=digest("stale-pause-request"),
        now_ms=1_000_020,
    )
    with pytest.raises(SpeechReconciliationRepositoryError, match="speech_reconciliation_fence_stale"):
        stale_repo.complete(
            tenant_id=stale_spec.tenant_id,
            owner_subject=stale_spec.owner_subject,
            job_contract=stale_contract,
            result=result_contract(
                stale_contract,
                status="cancelled",
                dataset_manifest_digest=None,
                dataset_artifact_ref=None,
                evaluation_digest=None,
                resolved_count=0,
                unresolved_count=0,
                rejected_count=0,
                reason_code="speech_reconciliation_cancelled",
            ),
            publication_authorized=False,
            now_ms=1_000_030,
        )


def test_pause_atomically_fences_attempt_and_rejects_late_result() -> None:
    repo = SpeechReconciliationRepository()
    spec = _spec("pause-fence")
    job, _ = repo.create_job(spec, now_ms=1_000_000)
    attempt = repo.claim_attempt(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
        expected_job_version=job.version,
        worker_id_digest=digest("worker-pause"),
        worker_capability_digest=digest("capability-pause"),
        location_digest=digest("location-pause"),
        resource_profile_digest=digest("resources-pause"),
        fencing_token_digest=digest("fence-pause"),
        lease_expires_at_ms=1_100_000,
        now_ms=1_000_010,
    )
    running = repo.get_job(tenant_id=spec.tenant_id, owner_subject=spec.owner_subject, job_id=job.id)
    assert running is not None
    paused_result = repo.transition(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
        target_state="paused",
        stage="slow_asr",
        reason_code="user_paused",
        expected_version=running.version,
        idempotency_key_digest=digest("pause-fence-key"),
        request_digest=digest("pause-fence-request"),
        now_ms=1_000_020,
    )
    paused = paused_result.job
    assert paused.active_attempt_id is None
    contract = job_contract(
        job_id=job.id,
        attempt_id=attempt.id,
        fencing_epoch=attempt.fencing_epoch,
        fencing_token_digest=attempt.fencing_token_digest,
        consent_id=spec.consent_id,
        input_manifest_digest=spec.input_manifest_digest,
        input_lineage_digest=spec.input_lineage_digest,
        input_artifact_ref=spec.input_artifact_ref,
        policy_digest=spec.policy_digest,
        source_duration_ms=spec.source_duration_ms,
        deadline_at_ms=spec.deadline_at_ms,
    )
    with pytest.raises(SpeechReconciliationRepositoryError, match="speech_reconciliation_fence_stale"):
        repo.complete(
            tenant_id=spec.tenant_id,
            owner_subject=spec.owner_subject,
            job_contract=contract,
            result=result_contract(contract),
            publication_authorized=True,
            now_ms=1_000_030,
        )


def test_mutation_receipt_is_exact_concurrent_and_conflict_safe_across_repositories() -> None:
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(InMemorySemanticMediaAuditRepository(), clock_ms=lambda: 1_000_020),
        secret=b"speech-reconciliation-race-audit" * 2,
    )
    first_hub = SpeechReconciliationRepository(audit=audit)
    second_hub = SpeechReconciliationRepository(audit=audit)
    spec = _spec("mutation-race")
    job, _ = first_hub.create_job(spec, now_ms=1_000_000)
    key_digest = digest("mutation-race-key")
    request_digest = digest("mutation-race-request")

    def pause(repository: SpeechReconciliationRepository):
        return repository.transition(
            tenant_id=spec.tenant_id,
            owner_subject=spec.owner_subject,
            job_id=job.id,
            target_state="paused",
            stage="admission",
            reason_code="speech_reconciliation_user_paused",
            expected_version=job.version,
            idempotency_key_digest=key_digest,
            request_digest=request_digest,
            now_ms=1_000_020,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(pause, (first_hub, second_hub)))

    assert sorted(result.applied for result in outcomes) == [False, True]
    assert outcomes[0].job == outcomes[1].job
    assert outcomes[0].job.state == "paused" and outcomes[0].job.version == job.version + 1

    resumed = first_hub.transition(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
        target_state="queued",
        stage="admission",
        reason_code="speech_reconciliation_user_resumed",
        expected_version=outcomes[0].job.version,
        idempotency_key_digest=digest("mutation-race-resume-key"),
        request_digest=digest("mutation-race-resume-request"),
        now_ms=1_000_030,
    )
    assert resumed.applied and resumed.job.state == "queued"

    exact_replay = second_hub.transition(
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        job_id=job.id,
        target_state="paused",
        stage="admission",
        reason_code="speech_reconciliation_user_paused",
        expected_version=job.version,
        idempotency_key_digest=key_digest,
        request_digest=request_digest,
        now_ms=1_000_040,
    )
    assert not exact_replay.applied
    assert exact_replay.job == outcomes[0].job
    assert exact_replay.job.state == "paused"

    with pytest.raises(
        SpeechReconciliationRepositoryError,
        match="speech_reconciliation_idempotency_conflict",
    ) as conflict:
        second_hub.transition(
            tenant_id=spec.tenant_id,
            owner_subject=spec.owner_subject,
            job_id=job.id,
            target_state="paused",
            stage="admission",
            reason_code="speech_reconciliation_user_paused",
            expected_version=resumed.job.version,
            idempotency_key_digest=key_digest,
            request_digest=digest("mutation-race-divergent-request"),
            now_ms=1_000_050,
        )
    assert conflict.value.status_code == 409

    with Session(engine) as session:
        receipts = session.exec(
            select(SpeechReconciliationMutationDB).where(
                SpeechReconciliationMutationDB.job_id == job.id,
                SpeechReconciliationMutationDB.operation == "pause",
            )
        ).all()
        pause_audits = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.job_ref == audit.digest("job", job.id),
                SemanticMediaAuditOutboxDB.transition == "paused",
            )
        ).all()
    assert len(receipts) == 1
    assert len(pause_audits) == 1
    receipt = receipts[0]
    assert receipt.request_digest == request_digest
    assert receipt.result_job_version == job.version + 1
    assert receipt.result_snapshot["state"] == "paused"
