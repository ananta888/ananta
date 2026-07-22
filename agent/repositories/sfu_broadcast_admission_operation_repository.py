"""Durable operation journal for cross-repository SFU admission sagas."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_broadcast_admission_operations import SfuBroadcastAdmissionOperationDB


class SfuBroadcastAdmissionOperationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastAdmissionOperationCommand:
    tenant_id: str
    room_id: str
    actor_id: str
    operation: str
    idempotency_key: str
    request: Mapping[str, object]
    expected_version: int
    deadline_at: float


@dataclass(frozen=True, slots=True)
class SfuBroadcastAdmissionOperationRecord:
    id: str
    tenant_id: str
    room_id: str
    operation: str
    status: str
    current_step: str
    applied_steps: tuple[str, ...]
    external_request_ids: Mapping[str, str]
    bindings: Mapping[str, object]
    compensation: Mapping[str, object]
    reason_code: str | None
    deadline_at: float
    version: int


class SfuBroadcastAdmissionOperationRepositoryPort(Protocol):
    def begin(self, command: SfuBroadcastAdmissionOperationCommand, *, now: float) -> SfuBroadcastAdmissionOperationRecord: ...

    def advance(
        self,
        operation_id: str,
        *,
        expected_version: int,
        step: str,
        external_request_id: str | None,
        bindings: Mapping[str, object],
        now: float,
    ) -> SfuBroadcastAdmissionOperationRecord: ...

    def finish(
        self,
        operation_id: str,
        *,
        expected_version: int,
        status: str,
        reason_code: str,
        result_digest: str | None,
        compensation: Mapping[str, object],
        now: float,
    ) -> SfuBroadcastAdmissionOperationRecord: ...

    def open(self, *, limit: int, now: float) -> tuple[SfuBroadcastAdmissionOperationRecord, ...]: ...


class SqlSfuBroadcastAdmissionOperationRepository:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def begin(self, command: SfuBroadcastAdmissionOperationCommand, *, now: float) -> SfuBroadcastAdmissionOperationRecord:
        if command.deadline_at <= now or command.operation not in {"join", "publish", "subscribe"}:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_invalid")
        key_digest = _digest_text(command.idempotency_key)
        request_digest = _digest_json(command.request)
        try:
            with Session(self._engine) as db:
                existing = db.exec(
                    select(SfuBroadcastAdmissionOperationDB).where(
                        SfuBroadcastAdmissionOperationDB.tenant_id == command.tenant_id,
                        SfuBroadcastAdmissionOperationDB.idempotency_key_digest == key_digest,
                    )
                ).one_or_none()
                if existing is not None:
                    if existing.request_digest != request_digest or existing.operation != command.operation:
                        raise SfuBroadcastAdmissionOperationError("sfu_admission_idempotency_conflict")
                    return _record(existing)
                row = SfuBroadcastAdmissionOperationDB(
                    tenant_id=command.tenant_id,
                    room_id=command.room_id,
                    actor_digest=_digest_text(command.actor_id),
                    operation=command.operation,
                    idempotency_key_digest=key_digest,
                    request_digest=request_digest,
                    expected_version=command.expected_version,
                    deadline_at=command.deadline_at,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                return _record(row)
        except SfuBroadcastAdmissionOperationError:
            raise
        except IntegrityError as exc:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_cas_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_store_unavailable") from exc

    def advance(
        self,
        operation_id: str,
        *,
        expected_version: int,
        step: str,
        external_request_id: str | None,
        bindings: Mapping[str, object],
        now: float,
    ) -> SfuBroadcastAdmissionOperationRecord:
        try:
            with Session(self._engine) as db:
                row = db.get(SfuBroadcastAdmissionOperationDB, operation_id)
                if row is None or row.version != expected_version or row.status != "open":
                    raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_cas_conflict")
                steps = list(row.applied_steps)
                if step not in steps:
                    steps.append(step)
                request_ids = dict(row.external_request_ids)
                if external_request_id:
                    request_ids[step] = external_request_id
                merged = dict(row.bindings_json)
                merged.update(dict(bindings))
                row.current_step = step
                row.applied_steps = steps
                row.external_request_ids = request_ids
                row.bindings_json = merged
                row.version += 1
                row.updated_at = now
                db.add(row)
                db.commit()
                db.refresh(row)
                return _record(row)
        except SfuBroadcastAdmissionOperationError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_store_unavailable") from exc

    def finish(
        self,
        operation_id: str,
        *,
        expected_version: int,
        status: str,
        reason_code: str,
        result_digest: str | None,
        compensation: Mapping[str, object],
        now: float,
    ) -> SfuBroadcastAdmissionOperationRecord:
        if status not in {"completed", "compensated", "failed"}:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_status_invalid")
        try:
            with Session(self._engine) as db:
                result = db.exec(
                    sa.update(SfuBroadcastAdmissionOperationDB)
                    .where(
                        SfuBroadcastAdmissionOperationDB.id == operation_id,
                        SfuBroadcastAdmissionOperationDB.version == expected_version,
                        SfuBroadcastAdmissionOperationDB.status.in_(("open", "failed")),
                    )
                    .values(
                        status=status,
                        current_step=status,
                        reason_code=reason_code,
                        result_digest=result_digest,
                        compensation_json=dict(compensation),
                        completed_at=now,
                        updated_at=now,
                        version=SfuBroadcastAdmissionOperationDB.version + 1,
                    )
                )
                if result.rowcount != 1:
                    db.rollback()
                    current = db.get(SfuBroadcastAdmissionOperationDB, operation_id)
                    if current is not None and current.status == status:
                        return _record(current)
                    raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_cas_conflict")
                db.commit()
                return _record(db.get(SfuBroadcastAdmissionOperationDB, operation_id))
        except SfuBroadcastAdmissionOperationError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_store_unavailable") from exc

    def open(self, *, limit: int, now: float) -> tuple[SfuBroadcastAdmissionOperationRecord, ...]:
        if not 1 <= limit <= 500:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_recovery_limit_invalid")
        try:
            with Session(self._engine) as db:
                rows = db.exec(
                    select(SfuBroadcastAdmissionOperationDB)
                    .where(SfuBroadcastAdmissionOperationDB.status.in_(("open", "failed")))
                    .order_by(SfuBroadcastAdmissionOperationDB.deadline_at, SfuBroadcastAdmissionOperationDB.id)
                    .limit(limit)
                ).all()
                return tuple(_record(row) for row in rows)
        except SQLAlchemyError as exc:
            raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_store_unavailable") from exc


def _record(row: SfuBroadcastAdmissionOperationDB | None) -> SfuBroadcastAdmissionOperationRecord:
    if row is None:
        raise SfuBroadcastAdmissionOperationError("sfu_admission_operation_not_found")
    return SfuBroadcastAdmissionOperationRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        room_id=row.room_id,
        operation=row.operation,
        status=row.status,
        current_step=row.current_step,
        applied_steps=tuple(row.applied_steps),
        external_request_ids=dict(row.external_request_ids),
        bindings=dict(row.bindings_json),
        compensation=dict(row.compensation_json),
        reason_code=row.reason_code,
        deadline_at=row.deadline_at,
        version=row.version,
    )


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _digest_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()
    ).hexdigest()


__all__ = [
    "SfuBroadcastAdmissionOperationCommand",
    "SfuBroadcastAdmissionOperationError",
    "SfuBroadcastAdmissionOperationRecord",
    "SfuBroadcastAdmissionOperationRepositoryPort",
    "SqlSfuBroadcastAdmissionOperationRepository",
]
