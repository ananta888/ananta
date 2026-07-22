"""SQL lease and fencing adapter for Hub-owned SFU background jobs."""

from __future__ import annotations

import hashlib
import time

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_broadcast_background_jobs import SfuBroadcastBackgroundJobDB
from agent.services.sfu_broadcast_background_job_port import (
    SfuBroadcastBackgroundJobLease,
    SfuBroadcastBackgroundJobPort,
    SfuBroadcastBackgroundJobSpec,
)


class SfuBroadcastBackgroundJobError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlSfuBroadcastBackgroundJobRepository:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def claim(
        self, spec: SfuBroadcastBackgroundJobSpec, *, owner_id: str, now: float
    ) -> SfuBroadcastBackgroundJobLease | None:
        _validate_spec(spec)
        job_id = _job_id(spec.name, spec.partition_key)
        try:
            with Session(self._engine) as db:
                row = db.get(SfuBroadcastBackgroundJobDB, job_id)
                if row is None:
                    row = SfuBroadcastBackgroundJobDB(
                        id=job_id,
                        job_name=spec.name,
                        partition_key=spec.partition_key,
                        enabled=spec.enabled,
                        interval_ms_min=spec.interval_ms_min,
                        batch_size_max=spec.batch_size_max,
                        runtime_deadline_ms=spec.runtime_deadline_ms,
                        retry_max=spec.retry_max,
                        backoff_ms=spec.backoff_ms,
                        jitter_ms=spec.jitter_ms,
                        retention_seconds=spec.retention_seconds,
                        next_run_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(row)
                    db.commit()
                    db.refresh(row)
                else:
                    row.enabled = spec.enabled
                    row.interval_ms_min = spec.interval_ms_min
                    row.batch_size_max = spec.batch_size_max
                    row.runtime_deadline_ms = spec.runtime_deadline_ms
                    row.retry_max = spec.retry_max
                    row.backoff_ms = spec.backoff_ms
                    row.jitter_ms = spec.jitter_ms
                    row.retention_seconds = spec.retention_seconds
                    db.add(row)
                    db.commit()
                    db.refresh(row)
                if not row.enabled or row.next_run_at > now:
                    return None
                if row.lease_expires_at is not None and row.lease_expires_at > now:
                    return None
                expected = row.version
                fence = row.fencing_token + 1
                expires = now + spec.lease_seconds
                result = db.exec(
                    sa.update(SfuBroadcastBackgroundJobDB)
                    .where(
                        SfuBroadcastBackgroundJobDB.id == job_id,
                        SfuBroadcastBackgroundJobDB.version == expected,
                        sa.or_(
                            SfuBroadcastBackgroundJobDB.lease_expires_at.is_(None),
                            SfuBroadcastBackgroundJobDB.lease_expires_at <= now,
                        ),
                    )
                    .values(
                        owner_id=owner_id,
                        fencing_token=fence,
                        lease_expires_at=expires,
                        attempt=SfuBroadcastBackgroundJobDB.attempt + 1,
                        last_status="running",
                        last_reason_code=None,
                        last_started_at=now,
                        version=SfuBroadcastBackgroundJobDB.version + 1,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    db.rollback()
                    return None
                db.commit()
                current = db.get(SfuBroadcastBackgroundJobDB, job_id)
                return _lease(current)
        except IntegrityError:
            return None
        except SQLAlchemyError as exc:
            raise SfuBroadcastBackgroundJobError("sfu_background_job_store_unavailable") from exc

    def lease_valid(self, lease: SfuBroadcastBackgroundJobLease, *, now: float) -> bool:
        try:
            with Session(self._engine) as db:
                row = db.get(SfuBroadcastBackgroundJobDB, lease.job_id)
                return bool(
                    row
                    and row.owner_id == lease.owner_id
                    and row.fencing_token == lease.fencing_token
                    and row.version == lease.version
                    and row.lease_expires_at is not None
                    and row.lease_expires_at > now
                )
        except SQLAlchemyError as exc:
            raise SfuBroadcastBackgroundJobError("sfu_background_job_store_unavailable") from exc

    def finish(
        self,
        lease: SfuBroadcastBackgroundJobLease,
        *,
        status: str,
        reason_code: str,
        resume_cursor: str | None,
        now: float,
    ) -> None:
        interval = 0.0
        try:
            with Session(self._engine) as db:
                current = db.get(SfuBroadcastBackgroundJobDB, lease.job_id)
                if current is None:
                    raise SfuBroadcastBackgroundJobError("sfu_background_job_lease_lost")
                interval = current.interval_ms_min / 1000.0
                if status == "completed":
                    delay = interval
                    next_attempt = 0
                else:
                    exponent = min(max(current.attempt - 1, 0), 10)
                    backoff = (current.backoff_ms / 1000.0) * (2 ** exponent)
                    jitter = (
                        int(hashlib.sha256(f"{lease.job_id}:{lease.fencing_token}".encode()).hexdigest()[:8], 16)
                        % (current.jitter_ms + 1)
                    ) / 1000.0
                    delay = min(interval, backoff + jitter)
                    next_attempt = 0 if current.attempt > current.retry_max else current.attempt
                result = db.exec(
                    sa.update(SfuBroadcastBackgroundJobDB)
                    .where(
                        SfuBroadcastBackgroundJobDB.id == lease.job_id,
                        SfuBroadcastBackgroundJobDB.owner_id == lease.owner_id,
                        SfuBroadcastBackgroundJobDB.fencing_token == lease.fencing_token,
                        SfuBroadcastBackgroundJobDB.version == lease.version,
                        SfuBroadcastBackgroundJobDB.lease_expires_at > now,
                    )
                    .values(
                        owner_id=None,
                        lease_expires_at=None,
                        resume_cursor=resume_cursor,
                        attempt=next_attempt,
                        last_status=status,
                        last_reason_code=reason_code,
                        last_finished_at=now,
                        next_run_at=now + delay,
                        version=SfuBroadcastBackgroundJobDB.version + 1,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    db.rollback()
                    raise SfuBroadcastBackgroundJobError("sfu_background_job_lease_lost")
                db.commit()
        except SfuBroadcastBackgroundJobError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastBackgroundJobError("sfu_background_job_store_unavailable") from exc

    def release_owner(self, owner_id: str, *, now: float) -> int:
        try:
            with Session(self._engine) as db:
                result = db.exec(
                    sa.update(SfuBroadcastBackgroundJobDB)
                    .where(SfuBroadcastBackgroundJobDB.owner_id == owner_id)
                    .values(
                        owner_id=None,
                        lease_expires_at=now,
                        last_status="shutdown",
                        last_reason_code="sfu_background_job_owner_shutdown",
                        version=SfuBroadcastBackgroundJobDB.version + 1,
                        updated_at=now,
                    )
                )
                db.commit()
                return int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise SfuBroadcastBackgroundJobError("sfu_background_job_store_unavailable") from exc


def _lease(row: SfuBroadcastBackgroundJobDB | None) -> SfuBroadcastBackgroundJobLease:
    if row is None or row.owner_id is None or row.lease_expires_at is None:
        raise SfuBroadcastBackgroundJobError("sfu_background_job_lease_missing")
    return SfuBroadcastBackgroundJobLease(
        job_id=row.id,
        name=row.job_name,
        partition_key=row.partition_key,
        owner_id=row.owner_id,
        fencing_token=row.fencing_token,
        version=row.version,
        lease_expires_at=row.lease_expires_at,
        resume_cursor=row.resume_cursor,
        batch_size_max=row.batch_size_max,
        runtime_deadline_ms=row.runtime_deadline_ms,
    )


def _validate_spec(spec: SfuBroadcastBackgroundJobSpec) -> None:
    if not spec.name or not spec.partition_key:
        raise SfuBroadcastBackgroundJobError("sfu_background_job_scope_invalid")
    if not 100 <= spec.interval_ms_min <= 86_400_000:
        raise SfuBroadcastBackgroundJobError("sfu_background_job_interval_invalid")
    if not 1 <= spec.batch_size_max <= 1000 or not 100 <= spec.runtime_deadline_ms <= 300_000:
        raise SfuBroadcastBackgroundJobError("sfu_background_job_bounds_invalid")
    if not 0 <= spec.retry_max <= 20 or not 1 <= spec.lease_seconds <= 3600:
        raise SfuBroadcastBackgroundJobError("sfu_background_job_retry_invalid")


def _job_id(name: str, partition_key: str) -> str:
    return "sfu-background-job-" + hashlib.sha256(f"{name}\0{partition_key}".encode()).hexdigest()


__all__ = [
    "SfuBroadcastBackgroundJobError",
    "SfuBroadcastBackgroundJobLease",
    "SfuBroadcastBackgroundJobPort",
    "SfuBroadcastBackgroundJobSpec",
    "SqlSfuBroadcastBackgroundJobRepository",
]
