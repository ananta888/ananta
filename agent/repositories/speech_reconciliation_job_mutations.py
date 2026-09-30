"""Idempotent operator mutations (pause/resume/cancel/reduce) of speech jobs."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import SpeechReconciliationAttemptDB
from agent.models.speech_reconciliation_state_machine import SpeechReconciliationStateMachine
from agent.repositories.speech_reconciliation_audit_stager import SpeechReconciliationAuditStager
from agent.repositories.speech_reconciliation_locking import locked_job, sqlite_write_guard
from agent.repositories.speech_reconciliation_mutation_receipts import (
    find_mutation_receipt,
    mutation_winner_or_raise,
    new_mutation_receipt,
    replay_mutation,
    require_mutation_digest,
    result_from_receipt,
    transition_operation,
)
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationMutationResult,
    SpeechReconciliationRepositoryError,
    resolve_now_ms,
)


class SpeechReconciliationJobMutations:
    """Applies receipt-backed state and compute-factor mutations to one job."""

    def __init__(
        self,
        *,
        states: SpeechReconciliationStateMachine,
        audit: SpeechReconciliationAuditStager,
    ) -> None:
        self._states = states
        self._audit = audit


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
        guard = sqlite_write_guard()
        with guard:
            return self._transition(
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

    def _transition(
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
        now = resolve_now_ms(now_ms)
        operation = transition_operation(target_state)
        require_mutation_digest(idempotency_key_digest, "speech_reconciliation_idempotency_digest_invalid")
        require_mutation_digest(request_digest, "speech_reconciliation_request_digest_invalid")
        with Session(engine) as session:
            row = locked_job(session, tenant_id, owner_subject, job_id)
            existing = find_mutation_receipt(
                session,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
            )
            if existing is not None:
                return replay_mutation(existing, request_digest=request_digest)
            transition = self._states.transition(row.state, target_state, stage=stage, reason_code=reason_code)
            if transition.duplicate:
                receipt = new_mutation_receipt(
                    row=row,
                    operation=operation,
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    now_ms=now,
                    state_changed=False,
                )
                session.add(receipt)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    return mutation_winner_or_raise(
                        session,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        job_id=job_id,
                        operation=operation,
                        idempotency_key_digest=idempotency_key_digest,
                        request_digest=request_digest,
                        cause=exc,
                    )
                return result_from_receipt(receipt, applied=False)
            if row.version != expected_version:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_version_stale")
            attempt = None
            affected_attempt_id = None
            affected_fencing_epoch = None
            if target_state in {"paused", "cancel_requested", "cancelled"} and row.active_attempt_id:
                affected_attempt_id = row.active_attempt_id
                affected_fencing_epoch = row.fencing_epoch
                attempt = session.exec(
                    select(SpeechReconciliationAttemptDB)
                    .where(
                        SpeechReconciliationAttemptDB.id == row.active_attempt_id,
                        SpeechReconciliationAttemptDB.job_id == row.id,
                    )
                    .with_for_update()
                ).first()
                if attempt is not None and attempt.state == "running":
                    attempt.state = "cancel_requested" if target_state == "cancel_requested" else "fenced"
                    attempt.version += 1
                    attempt.updated_at_ms = now
                    attempt.finished_at_ms = now
                    session.add(attempt)
                # Clearing the active pointer is the publication fence. A
                # resume must mint a new attempt and a higher fencing epoch.
                row.active_attempt_id = None
            row.state = target_state
            row.stage = stage
            row.reason_code = reason_code
            row.version += 1
            row.updated_at_ms = now
            if target_state in {"completed", "dataset_only_completed", "failed", "cancelled", "expired"}:
                row.finished_at_ms = now
            session.add(row)
            receipt = new_mutation_receipt(
                row=row,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
                now_ms=now,
                state_changed=True,
                affected_attempt_id=affected_attempt_id,
                affected_fencing_epoch=affected_fencing_epoch,
            )
            session.add(receipt)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=(
                        f"speech-reconciliation:mutation:{job_id}:{operation}:"
                        f"{idempotency_key_digest}"
                    ),
                    event_type="semantic_job",
                    transition=target_state,
                    reason_code=reason_code,
                    epoch=max(1, row.fencing_epoch),
                    lease_ref=affected_attempt_id,
                ),
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                return mutation_winner_or_raise(
                    session,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    job_id=job_id,
                    operation=operation,
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    cause=exc,
                )
            session.refresh(row)
            return result_from_receipt(receipt, applied=True)

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

        guard = sqlite_write_guard()
        with guard:
            return self._reduce_factor(
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

    def _reduce_factor(
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
        now = resolve_now_ms(now_ms)
        if not 1 <= max_compute_factor <= 100:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_factor_invalid", status_code=422)
        reason = str(reason_code or "").strip()
        if not reason or len(reason) > 128 or any(character.isspace() for character in reason):
            raise SpeechReconciliationRepositoryError("speech_reconciliation_reason_invalid", status_code=422)
        require_mutation_digest(idempotency_key_digest, "speech_reconciliation_idempotency_digest_invalid")
        require_mutation_digest(request_digest, "speech_reconciliation_request_digest_invalid")
        with Session(engine) as session:
            row = locked_job(session, tenant_id, owner_subject, job_id)
            existing = find_mutation_receipt(
                session,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                operation="reduce",
                idempotency_key_digest=idempotency_key_digest,
            )
            if existing is not None:
                return replay_mutation(existing, request_digest=request_digest)
            if row.state not in {"queued", "paused", "running"}:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_factor_state_invalid")
            if row.version != expected_version:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_version_stale")
            if max_compute_factor > row.max_compute_factor:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_factor_increase_forbidden")
            if max_compute_factor == row.max_compute_factor:
                receipt = new_mutation_receipt(
                    row=row,
                    operation="reduce",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    now_ms=now,
                    state_changed=False,
                )
                session.add(receipt)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    return mutation_winner_or_raise(
                        session,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        job_id=job_id,
                        operation="reduce",
                        idempotency_key_digest=idempotency_key_digest,
                        request_digest=request_digest,
                        cause=exc,
                    )
                return result_from_receipt(receipt, applied=False)
            row.max_compute_factor = max_compute_factor
            row.current_compute_factor = min(row.current_compute_factor, max_compute_factor)
            row.reason_code = reason
            row.version += 1
            row.updated_at_ms = now
            session.add(row)
            receipt = new_mutation_receipt(
                row=row,
                operation="reduce",
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
                now_ms=now,
                state_changed=True,
            )
            session.add(receipt)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=(
                        f"speech-reconciliation:mutation:{job_id}:reduce:"
                        f"{idempotency_key_digest}"
                    ),
                    event_type="semantic_budget",
                    transition="factor_reduced",
                    reason_code=reason,
                    epoch=max(1, row.fencing_epoch),
                    lease_ref=row.active_attempt_id,
                ),
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                return mutation_winner_or_raise(
                    session,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    job_id=job_id,
                    operation="reduce",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    cause=exc,
                )
            session.refresh(row)
            return result_from_receipt(receipt, applied=True)


__all__ = ["SpeechReconciliationJobMutations"]
