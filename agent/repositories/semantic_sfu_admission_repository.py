"""Durable CAS boundary for Hub-owned SFU admission projections."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SemanticSfuAdmissionReceiptDB, SemanticSfuRoomStateDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditEvent,
    same_idempotent_audit_request,
)


class SemanticSfuAdmissionRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(slots=True)
class SfuRoomState:
    revision: int = 0
    participants: dict[str, int] = field(default_factory=dict)
    publications: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def clone(self) -> "SfuRoomState":
        return SfuRoomState(
            revision=self.revision,
            participants=dict(self.participants),
            publications=json.loads(json.dumps(self.publications)),
            subscriptions=json.loads(json.dumps(self.subscriptions)),
        )


@dataclass(frozen=True, slots=True)
class SfuAdmissionReceipt:
    request_digest: str
    result: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SfuAdmissionCommitResult:
    status: str
    result: Mapping[str, Any] | None = None


class SfuAdmissionStatePort(Protocol):
    def load(self, tenant_id: str, session_id: str) -> SfuRoomState | None: ...

    def compare_and_swap(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        state: SfuRoomState,
    ) -> bool: ...

    def get_receipt(
        self,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        operation: str,
        idempotency_key: str,
    ) -> SfuAdmissionReceipt | None: ...

    def put_receipt(
        self,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        result: Mapping[str, Any],
        *,
        expires_at: float,
    ) -> None: ...

    def commit_mutation(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        state: SfuRoomState,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        result: Mapping[str, Any],
        expires_at: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SfuAdmissionCommitResult: ...


class InMemorySfuAdmissionStateRepository:
    """Substitutable deterministic repository for focused unit tests."""

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._rooms: dict[tuple[str, str], SfuRoomState] = {}
        self._receipts: dict[tuple[str, str, str, str, str], tuple[float, SfuAdmissionReceipt]] = {}
        self._audit_events: dict[str, SemanticMediaAuditEvent] = {}

    @property
    def audit_events(self) -> tuple[SemanticMediaAuditEvent, ...]:
        with self._lock:
            return tuple(self._audit_events.values())

    def load(self, tenant_id: str, session_id: str) -> SfuRoomState | None:
        with self._lock:
            found = self._rooms.get((tenant_id, session_id))
            return found.clone() if found else None

    def compare_and_swap(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        state: SfuRoomState,
    ) -> bool:
        with self._lock:
            current = self._rooms.get((tenant_id, session_id))
            revision = current.revision if current else 0
            if revision != expected_revision or state.revision != expected_revision + 1:
                return False
            self._rooms[(tenant_id, session_id)] = state.clone()
            return True

    def get_receipt(
        self,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        operation: str,
        idempotency_key: str,
    ) -> SfuAdmissionReceipt | None:
        key = (tenant_id, session_id, actor_id, operation, _digest_key(idempotency_key))
        with self._lock:
            found = self._receipts.get(key)
            if found is None:
                return None
            if found[0] <= self._clock():
                self._receipts.pop(key, None)
                return None
            return SfuAdmissionReceipt(found[1].request_digest, json.loads(json.dumps(found[1].result)))

    def put_receipt(
        self,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        result: Mapping[str, Any],
        *,
        expires_at: float,
    ) -> None:
        key = (tenant_id, session_id, actor_id, operation, _digest_key(idempotency_key))
        receipt = SfuAdmissionReceipt(request_digest, json.loads(json.dumps(result)))
        with self._lock:
            existing = self._receipts.get(key)
            if existing and existing[1].request_digest != request_digest:
                raise SemanticSfuAdmissionRepositoryError("sfu_idempotency_conflict")
            self._receipts[key] = (expires_at, receipt)

    def commit_mutation(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        state: SfuRoomState,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        result: Mapping[str, Any],
        expires_at: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SfuAdmissionCommitResult:
        receipt_key = (
            tenant_id,
            session_id,
            actor_id,
            operation,
            _digest_key(idempotency_key),
        )
        with self._lock:
            existing = self._receipts.get(receipt_key)
            if existing is not None and existing[0] > self._clock():
                if existing[1].request_digest != request_digest:
                    raise SemanticSfuAdmissionRepositoryError("sfu_idempotency_conflict")
                self._stage_audit(audit_event)
                return SfuAdmissionCommitResult("replayed", json.loads(json.dumps(existing[1].result)))
            self._validate_audit(audit_event)
            current = self._rooms.get((tenant_id, session_id))
            revision = current.revision if current else 0
            if revision != expected_revision or state.revision != expected_revision + 1:
                return SfuAdmissionCommitResult("conflict")
            stored_result = json.loads(json.dumps(result))
            self._rooms[(tenant_id, session_id)] = state.clone()
            self._receipts[receipt_key] = (
                expires_at,
                SfuAdmissionReceipt(request_digest, stored_result),
            )
            self._stage_audit(audit_event)
            return SfuAdmissionCommitResult("committed", stored_result)

    def _validate_audit(self, event: SemanticMediaAuditEvent | None) -> None:
        if event is None:
            return
        existing = self._audit_events.get(event.idempotency_digest)
        if existing is not None and not same_idempotent_audit_request(existing, event):
            raise SemanticMediaAuditError("audit_idempotency_conflict", status_code=409)

    def _stage_audit(self, event: SemanticMediaAuditEvent | None) -> None:
        self._validate_audit(event)
        if event is not None:
            self._audit_events[event.idempotency_digest] = event


class SqlSfuAdmissionStateRepository:
    """Multi-Hub SQL implementation using optimistic revision fencing."""

    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def load(self, tenant_id: str, session_id: str) -> SfuRoomState | None:
        with Session(self._engine) as db:
            row = db.get(SemanticSfuRoomStateDB, _scope_id(tenant_id, session_id))
            return _state_from_row(row) if row else None

    def compare_and_swap(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        state: SfuRoomState,
    ) -> bool:
        if state.revision != expected_revision + 1:
            raise SemanticSfuAdmissionRepositoryError("sfu_revision_invalid")
        now = self._clock()
        scope_id = _scope_id(tenant_id, session_id)
        with Session(self._engine) as db:
            if expected_revision == 0:
                db.add(
                    SemanticSfuRoomStateDB(
                        id=scope_id,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        revision=state.revision,
                        participants=dict(state.participants),
                        publications=json.loads(json.dumps(state.publications)),
                        subscriptions=json.loads(json.dumps(state.subscriptions)),
                        created_at=now,
                        updated_at=now,
                    )
                )
                try:
                    db.commit()
                    return True
                except IntegrityError:
                    db.rollback()
                    return False
            result = db.exec(
                sa.update(SemanticSfuRoomStateDB)
                .where(
                    SemanticSfuRoomStateDB.id == scope_id,
                    SemanticSfuRoomStateDB.tenant_id == tenant_id,
                    SemanticSfuRoomStateDB.session_id == session_id,
                    SemanticSfuRoomStateDB.revision == expected_revision,
                )
                .values(
                    revision=state.revision,
                    participants=dict(state.participants),
                    publications=json.loads(json.dumps(state.publications)),
                    subscriptions=json.loads(json.dumps(state.subscriptions)),
                    updated_at=now,
                )
            )
            db.commit()
            return int(getattr(result, "rowcount", 0) or 0) == 1

    def get_receipt(
        self,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        operation: str,
        idempotency_key: str,
    ) -> SfuAdmissionReceipt | None:
        row_id = _receipt_id(tenant_id, session_id, actor_id, operation, idempotency_key)
        with Session(self._engine) as db:
            row = db.get(SemanticSfuAdmissionReceiptDB, row_id)
            if row is None:
                return None
            if row.expires_at <= self._clock():
                db.delete(row)
                db.commit()
                return None
            return SfuAdmissionReceipt(row.request_digest, json.loads(json.dumps(row.result_payload)))

    def put_receipt(
        self,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        result: Mapping[str, Any],
        *,
        expires_at: float,
    ) -> None:
        row_id = _receipt_id(tenant_id, session_id, actor_id, operation, idempotency_key)
        with Session(self._engine) as db:
            existing = db.get(SemanticSfuAdmissionReceiptDB, row_id)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise SemanticSfuAdmissionRepositoryError("sfu_idempotency_conflict")
                return
            db.add(
                SemanticSfuAdmissionReceiptDB(
                    id=row_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    actor_id=actor_id,
                    operation=operation,
                    idempotency_key_digest=_digest_key(idempotency_key),
                    request_digest=request_digest,
                    result_payload=json.loads(json.dumps(result)),
                    expires_at=expires_at,
                    created_at=self._clock(),
                )
            )
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                existing = db.get(SemanticSfuAdmissionReceiptDB, row_id)
                if existing is not None and existing.request_digest == request_digest:
                    return
                raise SemanticSfuAdmissionRepositoryError("sfu_idempotency_conflict") from exc

    def commit_mutation(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        state: SfuRoomState,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        result: Mapping[str, Any],
        expires_at: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SfuAdmissionCommitResult:
        if state.revision != expected_revision + 1:
            raise SemanticSfuAdmissionRepositoryError("sfu_revision_invalid")
        scope_id = _scope_id(tenant_id, session_id)
        receipt_id = _receipt_id(tenant_id, session_id, actor_id, operation, idempotency_key)
        now = self._clock()
        with Session(self._engine) as db:
            existing = db.get(SemanticSfuAdmissionReceiptDB, receipt_id)
            if existing is not None and existing.expires_at > now:
                if existing.request_digest != request_digest:
                    raise SemanticSfuAdmissionRepositoryError("sfu_idempotency_conflict")
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
                    db.commit()
                return SfuAdmissionCommitResult("replayed", json.loads(json.dumps(existing.result_payload)))
            if existing is not None:
                db.delete(existing)
                db.flush()
            if expected_revision == 0:
                db.add(
                    SemanticSfuRoomStateDB(
                        id=scope_id,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        revision=state.revision,
                        participants=dict(state.participants),
                        publications=json.loads(json.dumps(state.publications)),
                        subscriptions=json.loads(json.dumps(state.subscriptions)),
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                updated = db.exec(
                    sa.update(SemanticSfuRoomStateDB)
                    .where(
                        SemanticSfuRoomStateDB.id == scope_id,
                        SemanticSfuRoomStateDB.tenant_id == tenant_id,
                        SemanticSfuRoomStateDB.session_id == session_id,
                        SemanticSfuRoomStateDB.revision == expected_revision,
                    )
                    .values(
                        revision=state.revision,
                        participants=dict(state.participants),
                        publications=json.loads(json.dumps(state.publications)),
                        subscriptions=json.loads(json.dumps(state.subscriptions)),
                        updated_at=now,
                    )
                )
                if int(getattr(updated, "rowcount", 0) or 0) != 1:
                    db.rollback()
                    return SfuAdmissionCommitResult("conflict")
            stored_result = json.loads(json.dumps(result))
            db.add(
                SemanticSfuAdmissionReceiptDB(
                    id=receipt_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    actor_id=actor_id,
                    operation=operation,
                    idempotency_key_digest=_digest_key(idempotency_key),
                    request_digest=request_digest,
                    result_payload=stored_result,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
            try:
                db.commit()
                return SfuAdmissionCommitResult("committed", stored_result)
            except IntegrityError:
                db.rollback()
        # A competing Hub may have committed the same idempotency key.  Read
        # once outside the failed transaction; otherwise expose a CAS conflict.
        replay = self.get_receipt(tenant_id, session_id, actor_id, operation, idempotency_key)
        if replay is not None:
            if replay.request_digest != request_digest:
                raise SemanticSfuAdmissionRepositoryError("sfu_idempotency_conflict")
            return SfuAdmissionCommitResult("replayed", replay.result)
        return SfuAdmissionCommitResult("conflict")

    def prune(self, *, limit: int = 1_000) -> int:
        if not 1 <= limit <= 10_000:
            raise SemanticSfuAdmissionRepositoryError("sfu_prune_limit_invalid")
        with Session(self._engine) as db:
            ids = db.exec(
                select(SemanticSfuAdmissionReceiptDB.id)
                .where(SemanticSfuAdmissionReceiptDB.expires_at <= self._clock())
                .order_by(SemanticSfuAdmissionReceiptDB.expires_at)
                .limit(limit)
            ).all()
            if not ids:
                return 0
            result = db.exec(sa.delete(SemanticSfuAdmissionReceiptDB).where(SemanticSfuAdmissionReceiptDB.id.in_(ids)))
            db.commit()
            return int(getattr(result, "rowcount", 0) or 0)


def _state_from_row(row: SemanticSfuRoomStateDB) -> SfuRoomState:
    return SfuRoomState(
        revision=row.revision,
        participants={str(key): int(value) for key, value in (row.participants or {}).items()},
        publications=json.loads(json.dumps(row.publications or {})),
        subscriptions=json.loads(json.dumps(row.subscriptions or {})),
    )


def _scope_id(tenant_id: str, session_id: str) -> str:
    return hashlib.sha256(f"{tenant_id}\0{session_id}".encode()).hexdigest()


def _digest_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _receipt_id(tenant: str, session: str, actor: str, operation: str, key: str) -> str:
    return hashlib.sha256(f"{tenant}\0{session}\0{actor}\0{operation}\0{key}".encode()).hexdigest()


__all__ = [
    "InMemorySfuAdmissionStateRepository",
    "SemanticSfuAdmissionRepositoryError",
    "SfuAdmissionCommitResult",
    "SfuAdmissionReceipt",
    "SfuAdmissionStatePort",
    "SfuRoomState",
    "SqlSfuAdmissionStateRepository",
]
