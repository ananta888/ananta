"""Transactional Hub repository for restart-stable speech reconciliation.

``SpeechReconciliationRepository`` is the public facade. It composes focused
collaborators, each owning one responsibility of the job/attempt authority:

* ``SpeechReconciliationJobAdmission`` - idempotent admission and job reads
* ``SpeechReconciliationJobMutations`` - receipt-backed operator mutations
* ``SpeechReconciliationAttemptLeases`` - fenced attempt lease lifecycle
* ``SpeechReconciliationAttemptOutcomes`` - checkpoints, quality, results
* ``SpeechReconciliationRecoveryStore`` - collector/recovery projections

All collaborators share one ``SpeechReconciliationAuditStager`` so audit
events are staged in the same database transaction as the mutation.
"""

from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationBudgetLedgerDB,
    SpeechReconciliationJobDB,
)
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.models.speech_reconciliation_state_machine import SpeechReconciliationStateMachine
from agent.ports.semantic_media_audit import SemanticMediaAuditPort
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    get_speech_evidence_lineage_repository,
)
from agent.repositories.speech_reconciliation_attempt_leases import SpeechReconciliationAttemptLeases
from agent.repositories.speech_reconciliation_attempt_outcomes import SpeechReconciliationAttemptOutcomes
from agent.repositories.speech_reconciliation_audit_stager import SpeechReconciliationAuditStager
from agent.repositories.speech_reconciliation_job_admission import SpeechReconciliationJobAdmission
from agent.repositories.speech_reconciliation_job_mutations import SpeechReconciliationJobMutations
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationAttemptRecord,
    SpeechReconciliationCollectibleAttempt,
    SpeechReconciliationJobCreate,
    SpeechReconciliationJobRecord,
    SpeechReconciliationMutationResult,
    SpeechReconciliationRepositoryError,
)
from agent.repositories.speech_reconciliation_recovery_store import SpeechReconciliationRecoveryStore
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationCheckpoint,
    SpeechReconciliationJob,
    SpeechReconciliationResult,
)


class SpeechReconciliationRepository:
    """Facade delegating each authority concern to its focused collaborator."""

    def __init__(
        self,
        *,
        lineage: SpeechEvidenceLineageRepository | None = None,
        audit: SemanticMediaAuditPort | None = None,
        states: SpeechReconciliationStateMachine | None = None,
        audit_stager: SpeechReconciliationAuditStager | None = None,
        admission: SpeechReconciliationJobAdmission | None = None,
        mutations: SpeechReconciliationJobMutations | None = None,
        leases: SpeechReconciliationAttemptLeases | None = None,
        outcomes: SpeechReconciliationAttemptOutcomes | None = None,
        recovery: SpeechReconciliationRecoveryStore | None = None,
    ) -> None:
        self._lineage = lineage or get_speech_evidence_lineage_repository()
        self._states = states or SpeechReconciliationStateMachine()
        self._audit = audit_stager or SpeechReconciliationAuditStager(audit)
        if audit_stager is not None and audit is not None:
            self._audit.configure(audit)
        self._admission = admission or SpeechReconciliationJobAdmission(lineage=self._lineage, audit=self._audit)
        self._mutations = mutations or SpeechReconciliationJobMutations(states=self._states, audit=self._audit)
        self._leases = leases or SpeechReconciliationAttemptLeases(states=self._states, audit=self._audit)
        self._outcomes = outcomes or SpeechReconciliationAttemptOutcomes(
            lineage=self._lineage, states=self._states, audit=self._audit
        )
        self._recovery = recovery or SpeechReconciliationRecoveryStore(audit=self._audit)

    @property
    def transactional_audit(self) -> bool:
        return self._audit.enabled

    def configure_audit(self, audit: SemanticMediaAuditPort | None) -> None:
        """Composition-root hook used before any Hub mutation is accepted."""

        self._audit.configure(audit)

    # -- job admission and reads -------------------------------------------------

    def create_job(
        self, spec: SpeechReconciliationJobCreate, *, now_ms: int | None = None
    ) -> tuple[SpeechReconciliationJobRecord, bool]:
        return self._admission.create_job(spec, now_ms=now_ms)

    def get_job(self, *, tenant_id: str, owner_subject: str, job_id: str) -> SpeechReconciliationJobRecord | None:
        return self._admission.get_job(tenant_id=tenant_id, owner_subject=owner_subject, job_id=job_id)

    def list_jobs(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[SpeechReconciliationJobRecord, ...]:
        return self._admission.list_jobs(
            tenant_id=tenant_id, owner_subject=owner_subject, offset=offset, limit=limit
        )

    # -- operator mutations ------------------------------------------------------

    def transition(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        target_state: str,
        stage: str,
        reason_code: str,
        expected_version: int,
        idempotency_key_digest: str,
        request_digest: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationMutationResult:
        return self._mutations.transition(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_id=job_id,
            target_state=target_state,
            stage=stage,
            reason_code=reason_code,
            expected_version=expected_version,
            idempotency_key_digest=idempotency_key_digest,
            request_digest=request_digest,
            now_ms=now_ms,
        )

    def reduce_factor(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        max_compute_factor: int,
        expected_version: int,
        reason_code: str,
        idempotency_key_digest: str,
        request_digest: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationMutationResult:
        """Atomically reduce future work; already consumed ledger units remain immutable."""

        return self._mutations.reduce_factor(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_id=job_id,
            max_compute_factor=max_compute_factor,
            expected_version=expected_version,
            reason_code=reason_code,
            idempotency_key_digest=idempotency_key_digest,
            request_digest=request_digest,
            now_ms=now_ms,
        )

    # -- attempt leases ----------------------------------------------------------

    def claim_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        expected_job_version: int,
        worker_id_digest: str,
        worker_capability_digest: str,
        location_digest: str,
        resource_profile_digest: str,
        fencing_token_digest: str,
        lease_expires_at_ms: int,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        return self._leases.claim_attempt(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_id=job_id,
            expected_job_version=expected_job_version,
            worker_id_digest=worker_id_digest,
            worker_capability_digest=worker_capability_digest,
            location_digest=location_digest,
            resource_profile_digest=resource_profile_digest,
            fencing_token_digest=fencing_token_digest,
            lease_expires_at_ms=lease_expires_at_ms,
            now_ms=now_ms,
        )

    def heartbeat(
        self,
        *,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        fencing_token_digest: str,
        expected_version: int,
        lease_expires_at_ms: int,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        return self._leases.heartbeat(
            job_id=job_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            fencing_token_digest=fencing_token_digest,
            expected_version=expected_version,
            lease_expires_at_ms=lease_expires_at_ms,
            now_ms=now_ms,
        )

    def pause_active_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> bool:
        """Atomically fence one attempt and pause its job under Hub authority."""

        return self._leases.pause_active_attempt(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_id=job_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            reason_code=reason_code,
            now_ms=now_ms,
        )

    def cancel_active_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> bool:
        """Atomically finish an explicitly cancelled attempt and job."""

        return self._leases.cancel_active_attempt(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_id=job_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            reason_code=reason_code,
            now_ms=now_ms,
        )

    def expire_stale_attempts(self, *, now_ms: int | None = None, limit: int = 100) -> tuple[str, ...]:
        return self._leases.expire_stale_attempts(now_ms=now_ms, limit=limit)

    def fence_attempt(
        self,
        attempt_id: str,
        *,
        reason_code: str,
        authority: str = "hub",
        now_ms: int | None = None,
    ) -> bool:
        return self._leases.fence_attempt(attempt_id, reason_code=reason_code, authority=authority, now_ms=now_ms)

    # -- attempt outcomes --------------------------------------------------------

    def save_checkpoint(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract,
        checkpoint: SpeechReconciliationCheckpoint,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        return self._outcomes.save_checkpoint(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_contract=job_contract,
            checkpoint=checkpoint,
            now_ms=now_ms,
        )

    def apply_quality_decision(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract: SpeechReconciliationJob,
        action: str,
        current_factor: int,
        next_factor: int,
        quality_score_micros: int,
        unresolved_count: int,
        unresolved_high_quality_conflicts: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationJobRecord:
        """Persist one Hub policy observation and optionally fence/requeue a wave."""

        return self._outcomes.apply_quality_decision(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_contract=job_contract,
            action=action,
            current_factor=current_factor,
            next_factor=next_factor,
            quality_score_micros=quality_score_micros,
            unresolved_count=unresolved_count,
            unresolved_high_quality_conflicts=unresolved_high_quality_conflicts,
            reason_code=reason_code,
            now_ms=now_ms,
        )

    def complete(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract,
        result: SpeechReconciliationResult,
        publication_authorized: bool,
        now_ms: int | None = None,
    ) -> SpeechReconciliationJobRecord:
        return self._outcomes.complete(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            job_contract=job_contract,
            result=result,
            publication_authorized=publication_authorized,
            now_ms=now_ms,
        )

    def latest_checkpoint_ref(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
    ) -> tuple[str, str, int] | None:
        return self._outcomes.latest_checkpoint_ref(
            tenant_id=tenant_id, owner_subject=owner_subject, job_id=job_id
        )

    # -- collector and recovery --------------------------------------------------

    def list_collectible_attempts(
        self,
        *,
        now_ms: int,
        limit: int = 100,
    ) -> tuple[SpeechReconciliationCollectibleAttempt, ...]:
        """Return only currently fenced attempts that may need poll/cancel work."""

        return self._recovery.list_collectible_attempts(now_ms=now_ms, limit=limit)

    def attempt_audit_binding(self, attempt_id: str) -> tuple[str, str, int, str] | None:
        """Return the minimum content-free scope needed for audit repair."""

        return self._recovery.attempt_audit_binding(attempt_id)

    def list_recovery_candidates(self, *, now_ms: int, limit: int):
        """Project persisted recovery facts without exposing payload content."""

        return self._recovery.list_recovery_candidates(now_ms=now_ms, limit=limit)

    def apply_recovery(self, candidate, action, *, authority: str) -> bool:
        return self._recovery.apply_recovery(candidate, action, authority=authority)


class SqlSpeechReconciliationBudgetRepository:
    """Append-only ledger snapshots plus atomic job-sequence CAS."""

    def __init__(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._owner_subject = owner_subject
        self._audit = audit

    @property
    def transactional_audit(self) -> bool:
        return self._audit is not None

    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechReconciliationBudgetLedgerDB)
                .where(
                    SpeechReconciliationBudgetLedgerDB.job_id == job_id,
                    SpeechReconciliationBudgetLedgerDB.tenant_id == self._tenant_id,
                    SpeechReconciliationBudgetLedgerDB.owner_subject == self._owner_subject,
                )
                .order_by(SpeechReconciliationBudgetLedgerDB.sequence.desc())
                .limit(1)
            ).first()
            return _ledger(row) if row is not None else None

    def compare_and_swap(
        self,
        *,
        expected_sequence: int | None,
        ledger: SpeechReconciliationBudgetLedger,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.id == ledger.job_id,
                    SpeechReconciliationJobDB.tenant_id == self._tenant_id,
                    SpeechReconciliationJobDB.owner_subject == self._owner_subject,
                )
                .with_for_update()
            ).first()
            if job is None:
                return False
            latest = session.exec(
                select(SpeechReconciliationBudgetLedgerDB.sequence)
                .where(SpeechReconciliationBudgetLedgerDB.job_id == ledger.job_id)
                .order_by(SpeechReconciliationBudgetLedgerDB.sequence.desc())
                .limit(1)
            ).first()
            if latest != expected_sequence:
                return False
            session.add(
                SpeechReconciliationBudgetLedgerDB(
                    job_id=ledger.job_id,
                    attempt_id=ledger.attempt_id,
                    tenant_id=self._tenant_id,
                    owner_subject=self._owner_subject,
                    fencing_epoch=ledger.fencing_epoch,
                    sequence=ledger.sequence,
                    stage=ledger.stage,
                    source_duration_ms=ledger.source_duration_ms,
                    compute_factor=ledger.compute_factor,
                    allocated=ledger.allocated.to_dict(),
                    reserved=ledger.reserved.to_dict(),
                    consumed=ledger.consumed.to_dict(),
                    remaining=ledger.remaining.to_dict(),
                )
            )
            job.ledger_sequence = ledger.sequence
            job.updated_at_ms = time.time_ns() // 1_000_000
            session.add(job)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True


def _ledger(row: SpeechReconciliationBudgetLedgerDB) -> SpeechReconciliationBudgetLedger:
    return SpeechReconciliationBudgetLedger.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": row.job_id,
            "attempt_id": row.attempt_id,
            "fencing_epoch": row.fencing_epoch,
            "sequence": row.sequence,
            "stage": row.stage,
            "source_duration_ms": row.source_duration_ms,
            "compute_factor": row.compute_factor,
            "allocated": row.allocated,
            "reserved": row.reserved,
            "consumed": row.consumed,
            "remaining": row.remaining,
        }
    )


__all__ = [
    "SpeechReconciliationAttemptRecord",
    "SpeechReconciliationCollectibleAttempt",
    "SpeechReconciliationJobCreate",
    "SpeechReconciliationJobRecord",
    "SpeechReconciliationMutationResult",
    "SpeechReconciliationRepository",
    "SpeechReconciliationRepositoryError",
    "SqlSpeechReconciliationBudgetRepository",
]
