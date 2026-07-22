"""CAS persistence for Hub-owned runtime feature projections."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_broadcast_flag_projections import (
    SfuBroadcastFlagProjectionDB,
    SfuBroadcastRuntimeProjectionStateDB,
)
from agent.services.sfu_broadcast_runtime_control_port import SfuRuntimeControlResult


class SfuBroadcastFlagProjectionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastFlagProjectionCommand:
    tenant_id: str
    target_runtime_id: str
    cluster_id: str
    region: str
    runtime_control_mode: str
    flag_version: int
    cohort_version: int
    config_digest: str
    config: Mapping[str, object]
    nonce: str
    priority: int
    retry_max: int
    ttl_seconds: float
    deadline_at: float
    minimum_fencing_token: int = 1


@dataclass(frozen=True, slots=True)
class SfuBroadcastFlagProjectionRecord:
    id: str
    tenant_id: str
    target_runtime_id: str
    cluster_id: str
    region: str
    runtime_control_mode: str
    flag_version: int
    cohort_version: int
    config_digest: str
    config: Mapping[str, object]
    nonce: str
    fencing_token: int
    priority: int
    attempt: int
    retry_max: int
    ttl_seconds: float
    deadline_at: float
    next_attempt_at: float
    status: str
    reason_code: str | None
    version: int


@dataclass(frozen=True, slots=True)
class SfuRuntimeProjectionAdmissionState:
    ready: bool
    reason_code: str
    flag_version: int
    cohort_version: int
    fencing_token: int
    expires_at: float | None


class SfuBroadcastFlagProjectionRepositoryPort(Protocol):
    def stage(self, command: SfuBroadcastFlagProjectionCommand, *, now: float) -> SfuBroadcastFlagProjectionRecord: ...

    def due(self, *, limit: int, now: float) -> tuple[SfuBroadcastFlagProjectionRecord, ...]: ...

    def mark_attempt(
        self,
        projection_id: str,
        *,
        expected_version: int,
        retry_delay_seconds: float,
        now: float,
    ) -> SfuBroadcastFlagProjectionRecord: ...

    def record_result(
        self,
        projection_id: str,
        *,
        expected_version: int,
        result: SfuRuntimeControlResult,
        now: float,
    ) -> SfuBroadcastFlagProjectionRecord: ...

    def admission_state(
        self,
        *,
        tenant_id: str,
        target_runtime_id: str,
        flag_version: int,
        cohort_version: int,
        now: float,
    ) -> SfuRuntimeProjectionAdmissionState: ...


class SqlSfuBroadcastFlagProjectionRepository:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def stage(self, command: SfuBroadcastFlagProjectionCommand, *, now: float) -> SfuBroadcastFlagProjectionRecord:
        _validate_command(command, now)
        try:
            with Session(self._engine) as db:
                existing = db.exec(
                    select(SfuBroadcastFlagProjectionDB).where(
                        SfuBroadcastFlagProjectionDB.tenant_id == command.tenant_id,
                        SfuBroadcastFlagProjectionDB.target_runtime_id == command.target_runtime_id,
                        SfuBroadcastFlagProjectionDB.flag_version == command.flag_version,
                        SfuBroadcastFlagProjectionDB.cohort_version == command.cohort_version,
                        SfuBroadcastFlagProjectionDB.config_digest == command.config_digest,
                    )
                ).one_or_none()
                if existing is not None:
                    if _config_digest(existing.config_json) != command.config_digest:
                        raise SfuBroadcastFlagProjectionError("sfu_flag_projection_idempotency_conflict")
                    return _projection(existing)

                state = db.exec(
                    select(SfuBroadcastRuntimeProjectionStateDB).where(
                        SfuBroadcastRuntimeProjectionStateDB.tenant_id == command.tenant_id,
                        SfuBroadcastRuntimeProjectionStateDB.target_runtime_id == command.target_runtime_id,
                    )
                ).one_or_none()
                if state is not None and (
                    command.flag_version < state.flag_version
                    or command.cohort_version < state.cohort_version
                ):
                    raise SfuBroadcastFlagProjectionError("sfu_flag_projection_stale_version")
                fence = max(command.minimum_fencing_token, (state.fencing_token + 1) if state else 1)
                row = SfuBroadcastFlagProjectionDB(
                    tenant_id=command.tenant_id,
                    target_runtime_id=command.target_runtime_id,
                    cluster_id=command.cluster_id,
                    region=command.region,
                    runtime_control_mode=command.runtime_control_mode,
                    flag_version=command.flag_version,
                    cohort_version=command.cohort_version,
                    config_digest=command.config_digest,
                    config_json=dict(command.config),
                    nonce=command.nonce,
                    fencing_token=fence,
                    priority=command.priority,
                    retry_max=command.retry_max,
                    ttl_seconds=command.ttl_seconds,
                    deadline_at=command.deadline_at,
                    next_attempt_at=now,
                    status="pending",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                if state is None:
                    state = SfuBroadcastRuntimeProjectionStateDB(
                        tenant_id=command.tenant_id,
                        target_runtime_id=command.target_runtime_id,
                        cluster_id=command.cluster_id,
                        region=command.region,
                        flag_version=command.flag_version,
                        cohort_version=command.cohort_version,
                        config_digest=command.config_digest,
                        fencing_token=fence,
                        admission_allowed=False,
                        status="pending",
                        reason_code="sfu_flag_projection_ack_pending",
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(state)
                else:
                    state.cluster_id = command.cluster_id
                    state.region = command.region
                    state.flag_version = command.flag_version
                    state.cohort_version = command.cohort_version
                    state.config_digest = command.config_digest
                    state.fencing_token = fence
                    state.admission_allowed = False
                    state.status = "pending"
                    state.reason_code = "sfu_flag_projection_ack_pending"
                    state.ack_expires_at = None
                    state.version += 1
                    state.updated_at = now
                    db.add(state)
                db.commit()
                db.refresh(row)
                return _projection(row)
        except SfuBroadcastFlagProjectionError:
            raise
        except IntegrityError as exc:
            try:
                with Session(self._engine) as db:
                    existing = db.exec(
                        select(SfuBroadcastFlagProjectionDB).where(
                            SfuBroadcastFlagProjectionDB.tenant_id == command.tenant_id,
                            SfuBroadcastFlagProjectionDB.target_runtime_id == command.target_runtime_id,
                            SfuBroadcastFlagProjectionDB.flag_version == command.flag_version,
                            SfuBroadcastFlagProjectionDB.cohort_version == command.cohort_version,
                            SfuBroadcastFlagProjectionDB.config_digest == command.config_digest,
                        )
                    ).one_or_none()
                    if existing is not None and _config_digest(existing.config_json) == command.config_digest:
                        return _projection(existing)
            except SQLAlchemyError:
                pass
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_cas_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_store_unavailable") from exc

    def due(self, *, limit: int, now: float) -> tuple[SfuBroadcastFlagProjectionRecord, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_batch_invalid")
        try:
            with Session(self._engine) as db:
                rows = db.exec(
                    select(SfuBroadcastFlagProjectionDB)
                    .where(
                        SfuBroadcastFlagProjectionDB.status.in_(("pending", "retry", "dispatching")),
                        SfuBroadcastFlagProjectionDB.next_attempt_at <= now,
                        SfuBroadcastFlagProjectionDB.deadline_at > now,
                        SfuBroadcastFlagProjectionDB.attempt <= SfuBroadcastFlagProjectionDB.retry_max,
                    )
                    .order_by(
                        SfuBroadcastFlagProjectionDB.priority.desc(),
                        SfuBroadcastFlagProjectionDB.created_at,
                        SfuBroadcastFlagProjectionDB.id,
                    )
                    .limit(limit)
                ).all()
                return tuple(_projection(row) for row in rows)
        except SQLAlchemyError as exc:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_store_unavailable") from exc

    def mark_attempt(
        self,
        projection_id: str,
        *,
        expected_version: int,
        retry_delay_seconds: float,
        now: float,
    ) -> SfuBroadcastFlagProjectionRecord:
        values = {
            "attempt": SfuBroadcastFlagProjectionDB.attempt + 1,
            "status": "dispatching",
            "next_attempt_at": now + max(0.05, retry_delay_seconds),
            "version": SfuBroadcastFlagProjectionDB.version + 1,
            "updated_at": now,
        }
        return self._cas_projection(projection_id, expected_version, values)

    def record_result(
        self,
        projection_id: str,
        *,
        expected_version: int,
        result: SfuRuntimeControlResult,
        now: float,
    ) -> SfuBroadcastFlagProjectionRecord:
        try:
            with Session(self._engine) as db:
                row = db.get(SfuBroadcastFlagProjectionDB, projection_id)
                if row is None:
                    raise SfuBroadcastFlagProjectionError("sfu_flag_projection_not_found")
                if row.version != expected_version:
                    raise SfuBroadcastFlagProjectionError("sfu_flag_projection_cas_conflict")
                matching = (
                    result.authenticated
                    and result.accepted
                    and result.target_runtime_id == row.target_runtime_id
                    and result.flag_version == row.flag_version
                    and result.cohort_version == row.cohort_version
                    and result.config_digest == row.config_digest
                    and result.nonce == row.nonce
                    and result.fencing_token == row.fencing_token
                )
                retryable = (
                    not matching
                    and row.attempt <= row.retry_max
                    and row.deadline_at > now
                )
                row.status = "acknowledged" if matching else ("retry" if retryable else "rejected")
                row.reason_code = "accepted" if matching else result.reason_code
                row.acknowledged_at = now if matching else None
                row.acknowledgement_digest = result.acknowledgement_digest if matching else None
                row.version += 1
                row.updated_at = now
                state = db.exec(
                    select(SfuBroadcastRuntimeProjectionStateDB).where(
                        SfuBroadcastRuntimeProjectionStateDB.tenant_id == row.tenant_id,
                        SfuBroadcastRuntimeProjectionStateDB.target_runtime_id == row.target_runtime_id,
                    )
                ).one()
                current = (
                    state.fencing_token == row.fencing_token
                    and state.flag_version == row.flag_version
                    and state.cohort_version == row.cohort_version
                    and state.config_digest == row.config_digest
                )
                if current:
                    state.admission_allowed = bool(matching)
                    state.status = row.status
                    state.reason_code = row.reason_code
                    state.ack_expires_at = now + row.ttl_seconds if matching else None
                    state.version += 1
                    state.updated_at = now
                    db.add(state)
                db.add(row)
                db.commit()
                db.refresh(row)
                return _projection(row)
        except SfuBroadcastFlagProjectionError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_store_unavailable") from exc

    def admission_state(
        self,
        *,
        tenant_id: str,
        target_runtime_id: str,
        flag_version: int,
        cohort_version: int,
        now: float,
    ) -> SfuRuntimeProjectionAdmissionState:
        try:
            with Session(self._engine) as db:
                row = db.exec(
                    select(SfuBroadcastRuntimeProjectionStateDB).where(
                        SfuBroadcastRuntimeProjectionStateDB.tenant_id == tenant_id,
                        SfuBroadcastRuntimeProjectionStateDB.target_runtime_id == target_runtime_id,
                    )
                ).one_or_none()
                if row is None:
                    return SfuRuntimeProjectionAdmissionState(False, "sfu_flag_projection_unknown", 0, 0, 0, None)
                ready = (
                    row.admission_allowed
                    and row.flag_version == flag_version
                    and row.cohort_version == cohort_version
                    and row.ack_expires_at is not None
                    and row.ack_expires_at > now
                )
                reason = "accepted" if ready else (
                    "sfu_flag_projection_ack_stale"
                    if row.ack_expires_at is not None and row.ack_expires_at <= now
                    else row.reason_code or "sfu_flag_projection_not_ready"
                )
                return SfuRuntimeProjectionAdmissionState(
                    ready, reason, row.flag_version, row.cohort_version, row.fencing_token, row.ack_expires_at
                )
        except SQLAlchemyError as exc:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_store_unavailable") from exc

    def _cas_projection(
        self, projection_id: str, expected_version: int, values: Mapping[str, object]
    ) -> SfuBroadcastFlagProjectionRecord:
        try:
            with Session(self._engine) as db:
                result = db.exec(
                    sa.update(SfuBroadcastFlagProjectionDB)
                    .where(
                        SfuBroadcastFlagProjectionDB.id == projection_id,
                        SfuBroadcastFlagProjectionDB.version == expected_version,
                    )
                    .values(**dict(values))
                )
                if result.rowcount != 1:
                    db.rollback()
                    raise SfuBroadcastFlagProjectionError("sfu_flag_projection_cas_conflict")
                db.commit()
                return _projection(db.get(SfuBroadcastFlagProjectionDB, projection_id))
        except SfuBroadcastFlagProjectionError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_store_unavailable") from exc


def _projection(row: SfuBroadcastFlagProjectionDB | None) -> SfuBroadcastFlagProjectionRecord:
    if row is None:
        raise SfuBroadcastFlagProjectionError("sfu_flag_projection_not_found")
    return SfuBroadcastFlagProjectionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        target_runtime_id=row.target_runtime_id,
        cluster_id=row.cluster_id,
        region=row.region,
        runtime_control_mode=row.runtime_control_mode,
        flag_version=row.flag_version,
        cohort_version=row.cohort_version,
        config_digest=row.config_digest,
        config=dict(row.config_json),
        nonce=row.nonce,
        fencing_token=row.fencing_token,
        priority=row.priority,
        attempt=row.attempt,
        retry_max=row.retry_max,
        ttl_seconds=row.ttl_seconds,
        deadline_at=row.deadline_at,
        next_attempt_at=row.next_attempt_at,
        status=row.status,
        reason_code=row.reason_code,
        version=row.version,
    )


def _validate_command(command: SfuBroadcastFlagProjectionCommand, now: float) -> None:
    for value in (command.tenant_id, command.target_runtime_id, command.cluster_id, command.region, command.nonce):
        if not isinstance(value, str) or not value or len(value) > 255:
            raise SfuBroadcastFlagProjectionError("sfu_flag_projection_command_invalid")
    if command.flag_version < 0 or command.cohort_version < 0 or command.deadline_at <= now:
        raise SfuBroadcastFlagProjectionError("sfu_flag_projection_version_invalid")
    if command.config_digest != _config_digest(command.config):
        raise SfuBroadcastFlagProjectionError("sfu_flag_projection_digest_invalid")
    if not 1 <= command.ttl_seconds <= 3600 or not 0 <= command.retry_max <= 20:
        raise SfuBroadcastFlagProjectionError("sfu_flag_projection_bounds_invalid")


def _config_digest(config: Mapping[str, object]) -> str:
    raw = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "SfuBroadcastFlagProjectionCommand",
    "SfuBroadcastFlagProjectionError",
    "SfuBroadcastFlagProjectionRecord",
    "SfuBroadcastFlagProjectionRepositoryPort",
    "SfuRuntimeProjectionAdmissionState",
    "SqlSfuBroadcastFlagProjectionRepository",
]
