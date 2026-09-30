"""Idempotency receipts for externally requested speech reconciliation mutations.

A receipt stores the job snapshot produced by the first accepted request so a
replay with the same digest returns the identical durable outcome without
repeating side effects.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.speech_reconciliation import (
    SpeechReconciliationJobDB,
    SpeechReconciliationMutationDB,
)
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationJobRecord,
    SpeechReconciliationMutationResult,
    SpeechReconciliationRepositoryError,
    job_record,
)


def transition_operation(target_state: str) -> str:
    try:
        return {
            "paused": "pause",
            "queued": "resume",
            "cancel_requested": "cancel",
            "cancelled": "cancel",
        }[target_state]
    except KeyError as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_mutation_operation_invalid",
            status_code=422,
        ) from exc


def require_mutation_digest(value: str, reason_code: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SpeechReconciliationRepositoryError(reason_code, status_code=422)


def find_mutation_receipt(
    session: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    job_id: str,
    operation: str,
    idempotency_key_digest: str,
) -> SpeechReconciliationMutationDB | None:
    return session.exec(
        select(SpeechReconciliationMutationDB)
        .where(
            SpeechReconciliationMutationDB.tenant_id == tenant_id,
            SpeechReconciliationMutationDB.owner_subject == owner_subject,
            SpeechReconciliationMutationDB.job_id == job_id,
            SpeechReconciliationMutationDB.operation == operation,
            SpeechReconciliationMutationDB.idempotency_key_digest == idempotency_key_digest,
        )
        .with_for_update()
    ).first()


def new_mutation_receipt(
    *,
    row: SpeechReconciliationJobDB,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
    now_ms: int,
    state_changed: bool,
    affected_attempt_id: str | None = None,
    affected_fencing_epoch: int | None = None,
) -> SpeechReconciliationMutationDB:
    snapshot = asdict(job_record(row))
    return SpeechReconciliationMutationDB(
        tenant_id=row.tenant_id,
        owner_subject=row.owner_subject,
        job_id=row.id,
        operation=operation,
        idempotency_key_digest=idempotency_key_digest,
        request_digest=request_digest,
        result_job_version=row.version,
        result_snapshot=snapshot,
        affected_attempt_id=affected_attempt_id,
        affected_fencing_epoch=affected_fencing_epoch,
        state_changed=state_changed,
        created_at_ms=now_ms,
    )


def job_from_snapshot(raw: Mapping[str, Any]) -> SpeechReconciliationJobRecord:
    expected = set(SpeechReconciliationJobRecord.__dataclass_fields__)
    if set(raw) != expected:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_mutation_receipt_corrupt",
            status_code=500,
        )
    try:
        return SpeechReconciliationJobRecord(**dict(raw))
    except (TypeError, ValueError) as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_mutation_receipt_corrupt",
            status_code=500,
        ) from exc


def result_from_receipt(
    receipt: SpeechReconciliationMutationDB,
    *,
    applied: bool,
) -> SpeechReconciliationMutationResult:
    return SpeechReconciliationMutationResult(
        job=job_from_snapshot(receipt.result_snapshot),
        applied=applied,
        affected_attempt_id=receipt.affected_attempt_id,
        affected_fencing_epoch=receipt.affected_fencing_epoch,
    )


def replay_mutation(
    receipt: SpeechReconciliationMutationDB,
    *,
    request_digest: str,
) -> SpeechReconciliationMutationResult:
    if receipt.request_digest != request_digest:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_idempotency_conflict")
    return result_from_receipt(receipt, applied=False)


def mutation_winner_or_raise(
    session: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    job_id: str,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
    cause: IntegrityError,
) -> SpeechReconciliationMutationResult:
    winner = find_mutation_receipt(
        session,
        tenant_id=tenant_id,
        owner_subject=owner_subject,
        job_id=job_id,
        operation=operation,
        idempotency_key_digest=idempotency_key_digest,
    )
    if winner is None:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_write_conflict") from cause
    return replay_mutation(winner, request_digest=request_digest)


__all__ = [
    "find_mutation_receipt",
    "job_from_snapshot",
    "mutation_winner_or_raise",
    "new_mutation_receipt",
    "replay_mutation",
    "require_mutation_digest",
    "result_from_receipt",
    "transition_operation",
]
