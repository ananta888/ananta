"""Session-scoped row helpers of the Hub semantic-compute lease authority.

Every helper operates inside a caller-owned SQL session and never commits;
the lease repository remains the owner of the transaction boundary.
"""

from __future__ import annotations

import hashlib

from sqlmodel import Session, select

from agent.db_models import (
    SemanticComputeLeaseDB,
    SemanticComputeLeaseMutationDB,
    SemanticComputeScheduleReceiptDB,
)
from agent.repositories.semantic_lease_models import (
    LeaseRequest,
    LeaseScheduleCommit,
    SemanticLeaseRepositoryError,
)


def lease_scope_key(request: LeaseRequest) -> str:
    fields = [
        request.tenant_id,
        request.session_id,
        str(request.epoch),
        request.task_type,
        request.audience,
        request.role,
    ]
    if request.role == "validator":
        fields.append(request.executor_id)
    raw = "\0".join(fields)
    return hashlib.sha256(raw.encode()).hexdigest()


def active_scope_key(scope_key: str, sequence_start: int, sequence_end: int) -> str:
    return hashlib.sha256(f"{scope_key}:{sequence_start}:{sequence_end}".encode()).hexdigest()


def required_lease(db: Session, lease_id: str) -> SemanticComputeLeaseDB:
    item = db.get(SemanticComputeLeaseDB, lease_id)
    if item is None:
        raise SemanticLeaseRepositoryError("lease_not_found")
    return item


def required_scoped_lease(db: Session, tenant_id: str, owner_subject: str, lease_id: str) -> SemanticComputeLeaseDB:
    item = db.exec(
        select(SemanticComputeLeaseDB).where(
            SemanticComputeLeaseDB.id == lease_id,
            SemanticComputeLeaseDB.tenant_id == tenant_id,
            SemanticComputeLeaseDB.owner_subject == owner_subject,
        )
    ).first()
    if item is None:
        raise SemanticLeaseRepositoryError("lease_not_found")
    return item


def find_schedule_replay(
    db: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    contract_id: str,
    key_digest: str,
    request_digest: str,
) -> SemanticComputeScheduleReceiptDB | None:
    item = db.exec(
        select(SemanticComputeScheduleReceiptDB).where(
            SemanticComputeScheduleReceiptDB.tenant_id == tenant_id,
            SemanticComputeScheduleReceiptDB.owner_subject == owner_subject,
            SemanticComputeScheduleReceiptDB.contract_id == contract_id,
            SemanticComputeScheduleReceiptDB.idempotency_key_digest == key_digest,
        )
    ).first()
    if item is not None and item.request_digest != request_digest:
        raise SemanticLeaseRepositoryError("idempotency_conflict")
    return item


def schedule_commit_from_receipt(
    db: Session,
    receipt: SemanticComputeScheduleReceiptDB,
    *,
    replayed: bool,
) -> LeaseScheduleCommit:
    payload = dict(receipt.result_payload or {})
    lease_ids = [str(value) for value in payload.get("lease_ids") or ()]
    leases: list[SemanticComputeLeaseDB] = []
    for lease_id in lease_ids:
        lease = db.get(SemanticComputeLeaseDB, lease_id)
        if lease is None or (
            lease.tenant_id != receipt.tenant_id
            or lease.owner_subject != receipt.owner_subject
            or lease.contract_id != receipt.contract_id
        ):
            raise SemanticLeaseRepositoryError("schedule_receipt_stale")
        leases.append(lease)
    if not leases:
        raise SemanticLeaseRepositoryError("schedule_receipt_stale")
    return LeaseScheduleCommit(tuple(leases), payload, replayed)


def find_mutation_replay(
    db: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    lease_id: str,
    operation: str,
    idempotency_key: str,
    request_digest: str,
) -> SemanticComputeLeaseMutationDB | None:
    key_digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
    item = db.exec(
        select(SemanticComputeLeaseMutationDB).where(
            SemanticComputeLeaseMutationDB.tenant_id == tenant_id,
            SemanticComputeLeaseMutationDB.owner_subject == owner_subject,
            SemanticComputeLeaseMutationDB.lease_id == lease_id,
            SemanticComputeLeaseMutationDB.operation == operation,
            SemanticComputeLeaseMutationDB.idempotency_key_digest == key_digest,
        )
    ).first()
    if item is not None and item.request_digest != request_digest:
        raise SemanticLeaseRepositoryError("idempotency_conflict")
    return item


def record_mutation(
    db: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    lease_id: str,
    operation: str,
    idempotency_key: str,
    request_digest: str,
    result_version: int,
) -> None:
    db.add(
        SemanticComputeLeaseMutationDB(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            lease_id=lease_id,
            operation=operation,
            idempotency_key_digest=hashlib.sha256(idempotency_key.encode()).hexdigest(),
            request_digest=request_digest,
            result_version=result_version,
        )
    )


def expire_lease_row(item: SemanticComputeLeaseDB, now: float) -> None:
    item.status = "expired"
    item.active_scope_key = None
    item.updated_at = now
    item.version += 1


__all__ = [
    "active_scope_key",
    "expire_lease_row",
    "find_mutation_replay",
    "find_schedule_replay",
    "lease_scope_key",
    "record_mutation",
    "required_lease",
    "required_scoped_lease",
    "schedule_commit_from_receipt",
]
