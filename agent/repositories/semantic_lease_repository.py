"""Crash-safe Hub lease authority for delegated semantic-compute tasks."""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import (
    SemanticComputeContractDB,
    SemanticComputeLeaseDB,
    SemanticComputeLeaseMutationDB,
    SemanticComputeScheduleReceiptDB,
    SemanticLeaseFenceDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)


class SemanticLeaseRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    tenant_id: str
    owner_subject: str
    contract_id: str
    contract_digest: str
    session_id: str
    epoch: int
    task_type: str
    audience: str
    role: str
    executor_id: str
    sequence_start: int
    sequence_end: int
    resource_budget: Mapping[str, int]
    ttl_seconds: float
    deadline_at: float


@dataclass(frozen=True, slots=True)
class LeaseScheduleCommit:
    """One atomic Hub scheduling result, including its replay projection."""

    leases: tuple[SemanticComputeLeaseDB, ...]
    result_payload: Mapping[str, object]
    replayed: bool


class SemanticLeaseRepository:
    """CAS lease store. Only Hub-side services may depend on this repository."""

    _sqlite_lock = threading.RLock()

    def __init__(
        self,
        *,
        db_engine=default_engine,
        clock=time.time,
        clock_skew_seconds: float = 2.0,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._engine = db_engine
        self._clock = clock
        self._clock_skew = max(0.0, min(float(clock_skew_seconds), 5.0))
        self._audit = audit

    def configure_audit(self, audit: SemanticMediaAuditPort | None) -> None:
        """Configure the Hub audit command factory at the composition boundary."""

        self._audit = audit

    @contextmanager
    def transaction_fence(self) -> Iterator[None]:
        """Serialize aggregate writes on SQLite; SQL row locks remain final."""

        with self._sqlite_lock:
            yield

    def acquire(
        self,
        request: LeaseRequest,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticComputeLeaseDB:
        self._validate_request(request)
        # SQLite cannot SELECT FOR UPDATE; the DB constraints remain the final
        # fence and this lock gives deterministic in-process test/runtime CAS.
        with self._sqlite_lock:
            now = self._clock()
            with Session(self._engine) as db:
                lease = self._acquire_in_session(
                    db,
                    request,
                    now=now,
                    audit_event=audit_event,
                )
                try:
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    raise SemanticLeaseRepositoryError("lease_overlap") from exc
                db.refresh(lease)
                return lease

    def schedule_once(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        contract_id: str,
        idempotency_key: str,
        request_digest: str,
        requests: tuple[LeaseRequest, ...],
        result_payload: Mapping[str, object],
        expires_at: float,
        result_factory: (Callable[[tuple[SemanticComputeLeaseDB, ...]], Mapping[str, object]] | None) = None,
    ) -> LeaseScheduleCommit:
        """Commit a complete role set, its receipt and all audit commands once.

        The receipt is deliberately written by the lease authority because it
        is the transaction boundary for this aggregate.  A concurrent retry
        either observes the committed receipt or loses on a database fence;
        it can never expose a partial role set.
        """

        if not requests or len(requests) > 4:
            raise SemanticLeaseRepositoryError("schedule_roles_invalid")
        if expires_at <= self._clock():
            raise SemanticLeaseRepositoryError("schedule_receipt_expired")
        for request in requests:
            self._validate_request(request)
            if (
                request.tenant_id != tenant_id
                or request.owner_subject != owner_subject
                or request.contract_id != contract_id
            ):
                raise SemanticLeaseRepositoryError("schedule_scope_mismatch")
        key_digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        with self._sqlite_lock:
            with Session(self._engine) as db:
                replay = self._schedule_replay(
                    db,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    contract_id=contract_id,
                    key_digest=key_digest,
                    request_digest=request_digest,
                )
                if replay is not None:
                    return self._schedule_commit_from_receipt(db, replay, replayed=True)
                contract = db.exec(
                    select(SemanticComputeContractDB)
                    .where(
                        SemanticComputeContractDB.id == contract_id,
                        SemanticComputeContractDB.tenant_id == tenant_id,
                        SemanticComputeContractDB.owner_subject == owner_subject,
                    )
                    .with_for_update()
                ).first()
                expected_revision = int(result_payload.get("contract_revision") or 0)
                expected_digest = requests[0].contract_digest
                if contract is None or (
                    contract.status != "active"
                    or contract.digest != expected_digest
                    or contract.revision != expected_revision
                ):
                    raise SemanticLeaseRepositoryError("stale_contract_authority")
                now = self._clock()
                leases = tuple(self._acquire_in_session(db, request, now=now) for request in requests)
                generated = dict(result_factory(leases)) if result_factory is not None else {}
                payload = {
                    **dict(result_payload),
                    **generated,
                    "lease_ids": [item.id for item in leases],
                }
                receipt = SemanticComputeScheduleReceiptDB(
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    contract_id=contract_id,
                    idempotency_key_digest=key_digest,
                    request_digest=request_digest,
                    result_payload=payload,
                    expires_at=expires_at,
                )
                db.add(receipt)
                try:
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    replay = self._schedule_replay(
                        db,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        contract_id=contract_id,
                        key_digest=key_digest,
                        request_digest=request_digest,
                    )
                    if replay is not None:
                        return self._schedule_commit_from_receipt(db, replay, replayed=True)
                    raise SemanticLeaseRepositoryError("schedule_commit_conflict") from exc
                for lease in leases:
                    db.refresh(lease)
                return LeaseScheduleCommit(leases, payload, False)

    def renew(
        self,
        *,
        lease_id: str,
        fencing_token: int,
        expected_version: int,
        ttl_seconds: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticComputeLeaseDB:
        if not 0 < ttl_seconds <= 300:
            raise SemanticLeaseRepositoryError("lease_ttl_invalid")
        now = self._clock()
        with Session(self._engine) as db:
            current = db.get(SemanticComputeLeaseDB, lease_id)
            if current is None:
                raise SemanticLeaseRepositoryError("lease_not_found")
            if current.expires_at <= now + self._clock_skew or current.deadline_at <= now:
                raise SemanticLeaseRepositoryError("lease_expired")
            result = db.exec(
                sa.update(SemanticComputeLeaseDB)
                .where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.fencing_token == fencing_token,
                    SemanticComputeLeaseDB.version == expected_version,
                )
                .values(
                    expires_at=min(now + ttl_seconds, current.deadline_at),
                    version=expected_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise SemanticLeaseRepositoryError("lease_cas_conflict")
            self._enqueue_transition(
                db,
                current,
                transition="renewed",
                reason_code="hub_lease_renewed",
                result_version=expected_version + 1,
                audit_event=audit_event,
            )
            db.commit()
            return self._required(db, lease_id)

    def revoke(
        self,
        *,
        lease_id: str,
        fencing_token: int,
        expected_version: int,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticComputeLeaseDB:
        now = self._clock()
        with Session(self._engine) as db:
            current = db.get(SemanticComputeLeaseDB, lease_id)
            if current is None:
                raise SemanticLeaseRepositoryError("lease_not_found")
            result = db.exec(
                sa.update(SemanticComputeLeaseDB)
                .where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.fencing_token == fencing_token,
                    SemanticComputeLeaseDB.version == expected_version,
                )
                .values(
                    status="revoked",
                    active_scope_key=None,
                    revoked_at=now,
                    updated_at=now,
                    version=expected_version + 1,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                current = db.get(SemanticComputeLeaseDB, lease_id)
                if current is not None and current.status == "revoked" and current.fencing_token == fencing_token:
                    return current
                raise SemanticLeaseRepositoryError("lease_cas_conflict")
            self._enqueue_transition(
                db,
                current,
                transition="revoked",
                reason_code="hub_lease_revoked",
                result_version=expected_version + 1,
                audit_event=audit_event,
            )
            db.commit()
            return self._required(db, lease_id)

    def revoke_scoped(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        lease_id: str,
        fencing_token: int,
        expected_version: int,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticComputeLeaseDB:
        """Tenant-scoped CAS revoke in one transaction."""

        now = self._clock()
        with Session(self._engine) as db:
            current = self._required_scoped(db, tenant_id, owner_subject, lease_id)
            result = db.exec(
                sa.update(SemanticComputeLeaseDB)
                .where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.fencing_token == fencing_token,
                    SemanticComputeLeaseDB.version == expected_version,
                )
                .values(
                    status="revoked",
                    active_scope_key=None,
                    revoked_at=now,
                    updated_at=now,
                    version=expected_version + 1,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise SemanticLeaseRepositoryError("lease_cas_conflict")
            self._enqueue_transition(
                db,
                current,
                transition="revoked",
                reason_code="hub_lease_revoked",
                result_version=expected_version + 1,
                audit_event=audit_event,
            )
            db.commit()
            return self._required_scoped(db, tenant_id, owner_subject, lease_id)

    def revoke_scoped_idempotent(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        lease_id: str,
        fencing_token: int,
        expected_version: int,
        idempotency_key: str,
        request_digest: str,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> tuple[SemanticComputeLeaseDB, bool]:
        now = self._clock()
        with Session(self._engine) as db:
            replay = self._mutation_replay(
                db,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                lease_id=lease_id,
                operation="revoke",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return self._required_scoped(db, tenant_id, owner_subject, lease_id), True
            result = db.exec(
                sa.update(SemanticComputeLeaseDB)
                .where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.fencing_token == fencing_token,
                    SemanticComputeLeaseDB.version == expected_version,
                )
                .values(
                    status="revoked",
                    active_scope_key=None,
                    revoked_at=now,
                    updated_at=now,
                    version=expected_version + 1,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                if db.get(SemanticComputeLeaseDB, lease_id) is None:
                    raise SemanticLeaseRepositoryError("lease_not_found")
                raise SemanticLeaseRepositoryError("lease_cas_conflict")
            self._record_mutation(
                db,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                lease_id=lease_id,
                operation="revoke",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                result_version=expected_version + 1,
            )
            current = self._required_scoped(db, tenant_id, owner_subject, lease_id)
            self._enqueue_transition(
                db,
                current,
                transition="revoked",
                reason_code="hub_lease_revoked",
                result_version=expected_version + 1,
                audit_event=audit_event,
                command_key=f"semantic-lease:revoke:{idempotency_key}",
            )
            db.commit()
            return self._required_scoped(db, tenant_id, owner_subject, lease_id), False

    def reduce(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        lease_id: str,
        fencing_token: int,
        expected_version: int,
        resource_budget: Mapping[str, int],
        expires_at: float | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticComputeLeaseDB:
        """Atomically reduce (never expand) a live lease's authority."""

        expected_budget = {"cpu_ms", "memory_bytes", "artifact_bytes"}
        if set(resource_budget) != expected_budget or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in resource_budget.values()
        ):
            raise SemanticLeaseRepositoryError("resource_budget_invalid")
        now = self._clock()
        with Session(self._engine) as db:
            current = db.exec(
                select(SemanticComputeLeaseDB).where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                )
            ).first()
            if current is None:
                raise SemanticLeaseRepositoryError("lease_not_found")
            if current.status != "active" or current.expires_at <= now + self._clock_skew:
                raise SemanticLeaseRepositoryError("lease_not_authorized")
            old_budget = dict(current.resource_budget or {})
            if any(int(resource_budget[key]) > int(old_budget.get(key, 0)) for key in expected_budget):
                raise SemanticLeaseRepositoryError("lease_expansion_forbidden")
            next_expiry = current.expires_at if expires_at is None else float(expires_at)
            if next_expiry > current.expires_at or next_expiry <= now:
                raise SemanticLeaseRepositoryError("lease_expansion_forbidden")
            result = db.exec(
                sa.update(SemanticComputeLeaseDB)
                .where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.fencing_token == fencing_token,
                    SemanticComputeLeaseDB.version == expected_version,
                )
                .values(
                    resource_budget=dict(resource_budget),
                    expires_at=next_expiry,
                    version=expected_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise SemanticLeaseRepositoryError("lease_cas_conflict")
            self._enqueue_transition(
                db,
                current,
                transition="reduced",
                reason_code="hub_authority_reduced",
                result_version=expected_version + 1,
                audit_event=audit_event,
            )
            db.commit()
            return self._required(db, lease_id)

    def reduce_idempotent(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        lease_id: str,
        fencing_token: int,
        expected_version: int,
        resource_budget: Mapping[str, int],
        expires_at: float | None,
        idempotency_key: str,
        request_digest: str,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> tuple[SemanticComputeLeaseDB, bool]:
        expected_budget = {"cpu_ms", "memory_bytes", "artifact_bytes"}
        if set(resource_budget) != expected_budget or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in resource_budget.values()
        ):
            raise SemanticLeaseRepositoryError("resource_budget_invalid")
        now = self._clock()
        with Session(self._engine) as db:
            replay = self._mutation_replay(
                db,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                lease_id=lease_id,
                operation="reduce",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return self._required_scoped(db, tenant_id, owner_subject, lease_id), True
            current = self._required_scoped(db, tenant_id, owner_subject, lease_id)
            if current.status != "active" or current.expires_at <= now + self._clock_skew:
                raise SemanticLeaseRepositoryError("lease_not_authorized")
            old_budget = dict(current.resource_budget or {})
            if any(int(resource_budget[key]) > int(old_budget.get(key, 0)) for key in expected_budget):
                raise SemanticLeaseRepositoryError("lease_expansion_forbidden")
            next_expiry = current.expires_at if expires_at is None else float(expires_at)
            if next_expiry > current.expires_at or next_expiry <= now:
                raise SemanticLeaseRepositoryError("lease_expansion_forbidden")
            result = db.exec(
                sa.update(SemanticComputeLeaseDB)
                .where(
                    SemanticComputeLeaseDB.id == lease_id,
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.fencing_token == fencing_token,
                    SemanticComputeLeaseDB.version == expected_version,
                )
                .values(
                    resource_budget=dict(resource_budget),
                    expires_at=next_expiry,
                    version=expected_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise SemanticLeaseRepositoryError("lease_cas_conflict")
            self._record_mutation(
                db,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                lease_id=lease_id,
                operation="reduce",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                result_version=expected_version + 1,
            )
            self._enqueue_transition(
                db,
                current,
                transition="reduced",
                reason_code="hub_authority_reduced",
                result_version=expected_version + 1,
                audit_event=audit_event,
                command_key=f"semantic-lease:reduce:{idempotency_key}",
            )
            db.commit()
            return self._required_scoped(db, tenant_id, owner_subject, lease_id), False

    def list_for_principal(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        session_id: str,
        epoch: int,
        contract_id: str | None = None,
        limit: int = 100,
    ) -> list[SemanticComputeLeaseDB]:
        if not 1 <= limit <= 200:
            raise SemanticLeaseRepositoryError("limit_invalid")
        with Session(self._engine) as db:
            statement = select(SemanticComputeLeaseDB).where(
                SemanticComputeLeaseDB.tenant_id == tenant_id,
                SemanticComputeLeaseDB.owner_subject == owner_subject,
                SemanticComputeLeaseDB.session_id == session_id,
                SemanticComputeLeaseDB.epoch == epoch,
            )
            if contract_id is not None:
                statement = statement.where(SemanticComputeLeaseDB.contract_id == contract_id)
            return list(
                db.exec(
                    statement.order_by(
                        SemanticComputeLeaseDB.issued_at.desc(),
                        SemanticComputeLeaseDB.id.desc(),
                    ).limit(limit)
                )
            )

    def get_scoped(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        lease_id: str,
    ) -> SemanticComputeLeaseDB:
        with Session(self._engine) as db:
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

    def active_assignment_counts(
        self,
        *,
        tenant_id: str,
        session_id: str,
        epoch: int,
        executor_ids: set[str],
    ) -> dict[str, int]:
        """Project current Hub leases for deterministic load-aware fairness."""

        normalized = {str(value).strip() for value in executor_ids if str(value).strip()}
        if len(normalized) > 128:
            raise SemanticLeaseRepositoryError("executor_limit_invalid")
        if not normalized:
            return {}
        now = self._clock()
        with Session(self._engine) as db:
            rows = db.exec(
                select(SemanticComputeLeaseDB.executor_id).where(
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.session_id == session_id,
                    SemanticComputeLeaseDB.epoch == epoch,
                    SemanticComputeLeaseDB.status == "active",
                    SemanticComputeLeaseDB.expires_at > now + self._clock_skew,
                    SemanticComputeLeaseDB.deadline_at > now,
                    SemanticComputeLeaseDB.executor_id.in_(normalized),
                )
            )
            counts: dict[str, int] = {}
            for executor_id in rows:
                rendered = str(executor_id)
                counts[rendered] = counts.get(rendered, 0) + 1
            return counts

    def revoke_contract_active(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        contract_id: str,
    ) -> int:
        """Fail-safe bulk fence used after contract revoke/fallback."""

        with Session(self._engine) as db:
            changed = self.revoke_contract_active_in_session(
                db,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                contract_id=contract_id,
            )
            db.commit()
            return changed

    def revoke_contract_active_in_session(
        self,
        db: Session,
        *,
        tenant_id: str,
        owner_subject: str,
        contract_id: str,
    ) -> int:
        """Stage contract-cascade fences in the caller-owned transaction."""

        now = self._clock()
        rows = list(
            db.exec(
                select(SemanticComputeLeaseDB).where(
                    SemanticComputeLeaseDB.tenant_id == tenant_id,
                    SemanticComputeLeaseDB.owner_subject == owner_subject,
                    SemanticComputeLeaseDB.contract_id == contract_id,
                    SemanticComputeLeaseDB.status == "active",
                )
            )
        )
        for item in rows:
            next_version = item.version + 1
            self._enqueue_transition(
                db,
                item,
                transition="revoked",
                reason_code="contract_revision_changed",
                result_version=next_version,
            )
            item.status = "revoked"
            item.active_scope_key = None
            item.revoked_at = now
            item.updated_at = now
            item.version = next_version
            db.add(item)
        return len(rows)

    def expire_due(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise SemanticLeaseRepositoryError("limit_invalid")
        now = self._clock()
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(SemanticComputeLeaseDB)
                    .where(
                        SemanticComputeLeaseDB.status == "active",
                        SemanticComputeLeaseDB.expires_at <= now + self._clock_skew,
                    )
                    .limit(limit)
                )
            )
            for row in rows:
                next_version = row.version + 1
                self._enqueue_transition(
                    db,
                    row,
                    transition="expired",
                    reason_code="lease_ttl_elapsed",
                    result_version=next_version,
                )
                self._expire_row(row, now)
                db.add(row)
            db.commit()
            return len(rows)

    def authorize_result(
        self,
        *,
        lease_id: str,
        contract_digest: str,
        fencing_token: int,
        session_id: str,
        epoch: int,
        task_type: str,
        audience: str,
        sequence: int | None = None,
    ) -> SemanticComputeLeaseDB:
        now = self._clock()
        with Session(self._engine) as db:
            lease = self._required(db, lease_id)
            bindings = (
                lease.contract_digest == contract_digest,
                lease.fencing_token == fencing_token,
                lease.session_id == session_id,
                lease.epoch == epoch,
                lease.task_type == task_type,
                lease.audience == audience,
            )
            if not all(bindings):
                raise SemanticLeaseRepositoryError("lease_binding_mismatch")
            if lease.status != "active" or lease.expires_at <= now + self._clock_skew or lease.deadline_at <= now:
                raise SemanticLeaseRepositoryError("lease_not_authorized")
            if sequence is not None and not lease.sequence_start <= sequence <= lease.sequence_end:
                raise SemanticLeaseRepositoryError("lease_sequence_mismatch")
            return lease

    def get(self, lease_id: str) -> SemanticComputeLeaseDB:
        with Session(self._engine) as db:
            return self._required(db, lease_id)

    def _acquire_in_session(
        self,
        db: Session,
        request: LeaseRequest,
        *,
        now: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticComputeLeaseDB:
        """Stage one fenced lease and its audit command without committing."""

        scope_key = self.scope_key(request)
        active_key = self._active_key(scope_key, request.sequence_start, request.sequence_end)
        fence = db.get(SemanticLeaseFenceDB, scope_key)
        if fence is None:
            last_token = 0
            if request.role == "validator":
                # Preserve monotonicity for validators issued with the legacy
                # role-wide scope before executor-specific fencing existed.
                previous = db.exec(
                    select(sa.func.max(SemanticComputeLeaseDB.fencing_token)).where(
                        SemanticComputeLeaseDB.tenant_id == request.tenant_id,
                        SemanticComputeLeaseDB.session_id == request.session_id,
                        SemanticComputeLeaseDB.epoch == request.epoch,
                        SemanticComputeLeaseDB.task_type == request.task_type,
                        SemanticComputeLeaseDB.audience == request.audience,
                        SemanticComputeLeaseDB.role == request.role,
                        SemanticComputeLeaseDB.executor_id == request.executor_id,
                    )
                ).one()
                last_token = int(previous or 0)
            fence = SemanticLeaseFenceDB(
                scope_key=scope_key,
                last_token=last_token,
            )
            db.add(fence)
            db.flush()
        else:
            db.exec(
                select(SemanticLeaseFenceDB).where(SemanticLeaseFenceDB.scope_key == scope_key).with_for_update()
            ).first()

        # Validator authority is intentionally executor-specific: independent
        # validators may inspect the same sequence, while one executor can
        # never obtain overlapping validator authority.  The explicit column
        # predicate also fences active rows written by the pre-v2 scope shape.
        active_statement = select(SemanticComputeLeaseDB).where(
            SemanticComputeLeaseDB.tenant_id == request.tenant_id,
            SemanticComputeLeaseDB.session_id == request.session_id,
            SemanticComputeLeaseDB.epoch == request.epoch,
            SemanticComputeLeaseDB.task_type == request.task_type,
            SemanticComputeLeaseDB.audience == request.audience,
            SemanticComputeLeaseDB.role == request.role,
            SemanticComputeLeaseDB.status == "active",
            SemanticComputeLeaseDB.sequence_start <= request.sequence_end,
            SemanticComputeLeaseDB.sequence_end >= request.sequence_start,
        )
        if request.role == "validator":
            active_statement = active_statement.where(SemanticComputeLeaseDB.executor_id == request.executor_id)
        active = db.exec(active_statement).first()
        if active is not None:
            if active.expires_at > now + self._clock_skew and active.deadline_at > now:
                raise SemanticLeaseRepositoryError("lease_overlap")
            next_version = active.version + 1
            self._enqueue_transition(
                db,
                active,
                transition="expired",
                reason_code="lease_superseded_after_expiry",
                result_version=next_version,
            )
            self._expire_row(active, now)
            db.add(active)

        fence.last_token += 1
        fence.updated_at = now
        db.add(fence)
        lease = SemanticComputeLeaseDB(
            tenant_id=request.tenant_id,
            owner_subject=request.owner_subject,
            contract_id=request.contract_id,
            contract_digest=request.contract_digest,
            session_id=request.session_id,
            epoch=request.epoch,
            task_type=request.task_type,
            audience=request.audience,
            role=request.role,
            executor_id=request.executor_id,
            sequence_start=request.sequence_start,
            sequence_end=request.sequence_end,
            fencing_token=fence.last_token,
            resource_budget=dict(request.resource_budget),
            scope_key=scope_key,
            active_scope_key=active_key,
            issued_at=now,
            expires_at=min(now + request.ttl_seconds, request.deadline_at),
            deadline_at=request.deadline_at,
            updated_at=now,
        )
        db.add(lease)
        self._enqueue_transition(
            db,
            lease,
            transition="acquired",
            reason_code=f"hub_scheduled_{request.role}",
            result_version=lease.version,
            audit_event=audit_event,
        )
        return lease

    def _enqueue_transition(
        self,
        db: Session,
        lease: SemanticComputeLeaseDB,
        *,
        transition: str,
        reason_code: str,
        result_version: int,
        audit_event: SemanticMediaAuditEvent | None = None,
        command_key: str | None = None,
    ) -> None:
        if audit_event is None and self._audit is None:
            return
        try:
            event = audit_event or self._audit.prepare_transition(  # type: ignore[union-attr]
                idempotency_key=(command_key or f"semantic-lease:{transition}:{lease.id}:v{result_version}"),
                tenant_id=lease.tenant_id,
                scope=f"semantic-media-session:{lease.session_id}",
                event_type="semantic_lease",
                transition=transition,
                reason_code=reason_code,
                epoch=lease.epoch,
                contract_ref=lease.contract_digest,
                lease_ref=lease.id,
            )
            SqlSemanticMediaAuditOutbox.enqueue_in_session(db, event)
        except Exception as exc:
            raise SemanticLeaseRepositoryError("semantic_audit_unavailable") from exc

    @staticmethod
    def _schedule_replay(
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

    @classmethod
    def _schedule_commit_from_receipt(
        cls,
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

    @staticmethod
    def scope_key(request: LeaseRequest) -> str:
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

    @staticmethod
    def _active_key(scope_key: str, sequence_start: int, sequence_end: int) -> str:
        return hashlib.sha256(f"{scope_key}:{sequence_start}:{sequence_end}".encode()).hexdigest()

    @staticmethod
    def _required(db: Session, lease_id: str) -> SemanticComputeLeaseDB:
        item = db.get(SemanticComputeLeaseDB, lease_id)
        if item is None:
            raise SemanticLeaseRepositoryError("lease_not_found")
        return item

    @staticmethod
    def _required_scoped(db: Session, tenant_id: str, owner_subject: str, lease_id: str) -> SemanticComputeLeaseDB:
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

    @staticmethod
    def _mutation_replay(
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

    @staticmethod
    def _record_mutation(
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

    @staticmethod
    def _expire_row(item: SemanticComputeLeaseDB, now: float) -> None:
        item.status = "expired"
        item.active_scope_key = None
        item.updated_at = now
        item.version += 1

    def _validate_request(self, request: LeaseRequest) -> None:
        if request.sequence_start < 0 or request.sequence_end < request.sequence_start:
            raise SemanticLeaseRepositoryError("sequence_range_invalid")
        if not 0 < request.ttl_seconds <= 300:
            raise SemanticLeaseRepositoryError("lease_ttl_invalid")
        if request.deadline_at <= self._clock() - self._clock_skew:
            raise SemanticLeaseRepositoryError("deadline_expired")
        if request.role not in {"primary", "validator", "standby"}:
            raise SemanticLeaseRepositoryError("role_invalid")
        if request.task_type not in {"visual_extract", "visual_validate", "speech_features", "speech_validate"}:
            raise SemanticLeaseRepositoryError("task_type_invalid")
        expected_budget = {"cpu_ms", "memory_bytes", "artifact_bytes"}
        if set(request.resource_budget) != expected_budget:
            raise SemanticLeaseRepositoryError("resource_budget_invalid")


_repository: SemanticLeaseRepository | None = None


def get_semantic_lease_repository(*, audit: SemanticMediaAuditPort | None = None) -> SemanticLeaseRepository:
    global _repository
    if _repository is None:
        _repository = SemanticLeaseRepository(audit=audit)
    elif audit is not None:
        _repository.configure_audit(audit)
    return _repository


__all__ = [
    "LeaseRequest",
    "LeaseScheduleCommit",
    "SemanticLeaseRepository",
    "SemanticLeaseRepositoryError",
    "get_semantic_lease_repository",
]
