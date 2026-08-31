from __future__ import annotations

import hashlib
import time
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select, update

from agent.database import engine as default_engine
from agent.db_models import (
    MlInternDatasetDB,
    MlInternTrainingAttemptDB,
    MlInternTrainingCapacityLeaseDB,
    MlInternTrainingEventDB,
    MlInternTrainingExecutionLeaseDB,
    MlInternTrainingJobDB,
)
from agent.repositories.ml_intern_training_serialization import (
    is_slot_or_idempotency_conflict as _is_slot_or_idempotency_conflict,
)
from agent.repositories.ml_intern_training_serialization import (
    serialized_sqlite_read as _serialized_sqlite_read,
)
from agent.repositories.ml_intern_training_serialization import (
    serialized_write as _serialized_write,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)

_DEFAULT_AUDIT: SemanticMediaAuditPort | None = None


class MlInternTrainingRepositoryConflict(RuntimeError):
    pass


class MlInternTrainingRepository:
    """SQL-backed, tenant-scoped persistence adapter for training control state."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        audit: SemanticMediaAuditPort | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = db_engine
        self._audit = audit
        self._clock = clock

    @_serialized_write
    def create_dataset(self, dataset: MlInternDatasetDB) -> tuple[MlInternDatasetDB, bool]:
        audit_event = self._dataset_event(
            dataset,
            transition="dataset_created",
            reason_code="training_dataset_created",
            epoch=dataset.version,
        )
        with Session(self._engine) as session:
            existing = session.exec(
                select(MlInternDatasetDB).where(
                    MlInternDatasetDB.tenant_id == dataset.tenant_id,
                    MlInternDatasetDB.owner_subject == dataset.owner_subject,
                    MlInternDatasetDB.content_sha256 == dataset.content_sha256,
                )
            ).first()
            if existing is not None:
                self._enqueue(
                    session,
                    self._dataset_event(
                        existing,
                        transition="dataset_created",
                        reason_code="training_dataset_created",
                        epoch=existing.version,
                    ),
                )
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing, True
            session.add(dataset)
            try:
                with session.no_autoflush:
                    self._enqueue(session, audit_event)
                session.commit()
                session.refresh(dataset)
                return dataset, False
            except IntegrityError:
                session.rollback()
                existing = session.exec(
                    select(MlInternDatasetDB).where(
                        MlInternDatasetDB.tenant_id == dataset.tenant_id,
                        MlInternDatasetDB.owner_subject == dataset.owner_subject,
                        MlInternDatasetDB.content_sha256 == dataset.content_sha256,
                    )
                ).first()
                if existing is None:
                    raise
                self._enqueue(
                    session,
                    self._dataset_event(
                        existing,
                        transition="dataset_created",
                        reason_code="training_dataset_created",
                        epoch=existing.version,
                    ),
                )
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing, True

    def get_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> MlInternDatasetDB | None:
        with Session(self._engine) as session:
            return session.exec(self._dataset_query(principal).where(MlInternDatasetDB.id == dataset_id)).first()

    def list_datasets(
        self, principal: MlInternTrainingPrincipal, *, limit: int, offset: int
    ) -> list[MlInternDatasetDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    self._dataset_query(principal)
                    .order_by(MlInternDatasetDB.created_at.desc())
                    .offset(max(0, offset))
                    .limit(max(1, min(limit, 200)))
                ).all()
            )

    @_serialized_write
    def save_dataset(self, dataset: MlInternDatasetDB, *, expected_version: int) -> MlInternDatasetDB:
        now = self._clock()
        values = dataset.model_dump(exclude={"id", "version", "created_at"})
        values.update(version=expected_version + 1, updated_at=now)
        audit_event = self._dataset_event(
            dataset,
            transition="dataset_updated",
            reason_code="training_dataset_updated",
            epoch=expected_version + 1,
        )
        with Session(self._engine) as session:
            result = session.exec(
                update(MlInternDatasetDB)
                .where(MlInternDatasetDB.id == dataset.id, MlInternDatasetDB.version == expected_version)
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("dataset_version_conflict")
            self._enqueue(session, audit_event)
            session.commit()
        saved = self.get_dataset(MlInternTrainingPrincipal(dataset.tenant_id, dataset.owner_subject), dataset.id)
        if saved is None:
            raise MlInternTrainingRepositoryConflict("dataset_disappeared")
        return saved

    @_serialized_write
    def delete_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> bool:
        with Session(self._engine) as session:
            dataset = session.exec(self._dataset_query(principal).where(MlInternDatasetDB.id == dataset_id)).first()
            if dataset is None:
                return False
            reference_count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(MlInternTrainingJobDB.dataset_id == dataset_id)
            ).one()
            if int(reference_count or 0):
                raise MlInternTrainingRepositoryConflict("dataset_referenced")
            self._enqueue(
                session,
                self._dataset_event(
                    dataset,
                    transition="dataset_deleted",
                    reason_code="training_dataset_deleted",
                    epoch=dataset.version + 1,
                ),
            )
            session.delete(dataset)
            session.commit()
            return True

    @_serialized_write
    def create_job(self, job: MlInternTrainingJobDB) -> tuple[MlInternTrainingJobDB, bool]:
        principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
        audit_event = self._job_event(
            job,
            transition="job_created",
            reason_code="training_job_created",
            epoch=job.version,
        )
        with Session(self._engine) as session:
            existing = self._job_by_idempotency(session, principal, job.idempotency_key_digest)
            if existing is not None:
                if existing.request_digest != job.request_digest:
                    raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                self._enqueue(
                    session,
                    self._job_event(
                        existing,
                        transition="job_created",
                        reason_code="training_job_created",
                        epoch=existing.version,
                    ),
                )
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing, True
            session.add(job)
            try:
                with session.no_autoflush:
                    self._enqueue(session, audit_event)
                session.commit()
                session.refresh(job)
                return job, False
            except IntegrityError:
                session.rollback()
                existing = self._job_by_idempotency(session, principal, job.idempotency_key_digest)
                if existing is None:
                    raise
                if existing.request_digest != job.request_digest:
                    raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                self._enqueue(
                    session,
                    self._job_event(
                        existing,
                        transition="job_created",
                        reason_code="training_job_created",
                        epoch=existing.version,
                    ),
                )
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing, True

    @_serialized_write
    def create_job_with_capacity(
        self,
        job: MlInternTrainingJobDB,
        *,
        outstanding_limit: int,
    ) -> tuple[MlInternTrainingJobDB, bool]:
        """Atomically insert a job and one globally unique capacity slot."""

        if not 1 <= outstanding_limit <= 10_016:
            raise ValueError("outstanding training capacity is outside its bounds")
        principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
        for slot in range(outstanding_limit):
            with Session(self._engine) as session:
                existing = self._job_by_idempotency(session, principal, job.idempotency_key_digest)
                if existing is not None:
                    if existing.request_digest != job.request_digest:
                        raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                    self._enqueue_job_and_capacity_replay(session, existing)
                    session.commit()
                    session.refresh(existing)
                    session.expunge(existing)
                    return existing, True
                candidate = MlInternTrainingJobDB.model_validate(job.model_dump())
                lease = MlInternTrainingCapacityLeaseDB(slot=slot, job_id=candidate.id)
                session.add(candidate)
                # The lease points at the job by foreign key, so the job row has
                # to exist before the lease is inserted.  Without this flush the
                # lease insert failed that key, and because every IntegrityError
                # in this block reads as "slot taken", the loop burned through
                # every slot and reported training_capacity_exhausted instead.
                session.flush()
                session.add(lease)
                try:
                    with session.no_autoflush:
                        self._enqueue(
                            session,
                            self._job_event(
                                candidate,
                                transition="job_created",
                                reason_code="training_job_created",
                                epoch=candidate.version,
                            ),
                        )
                        self._enqueue(
                            session,
                            self._capacity_event(
                                candidate,
                                lease,
                                transition="capacity_acquired",
                                reason_code="training_capacity_acquired",
                                epoch=lease.version,
                            ),
                        )
                    session.commit()
                    session.refresh(candidate)
                    return candidate, False
                except IntegrityError as exc:
                    session.rollback()
                    # Only a taken slot or a duplicate idempotency key mean
                    # "try the next slot".  Any other integrity failure is a
                    # different fault and must not be retried into
                    # training_capacity_exhausted, which is how a foreign-key
                    # violation on the lease spent years looking like a full
                    # queue.
                    if not _is_slot_or_idempotency_conflict(exc):
                        raise
                    existing = self._job_by_idempotency(
                        session,
                        principal,
                        job.idempotency_key_digest,
                    )
                    if existing is not None:
                        if existing.request_digest != job.request_digest:
                            raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                        self._enqueue_job_and_capacity_replay(session, existing)
                        session.commit()
                        session.refresh(existing)
                        session.expunge(existing)
                        return existing, True
        raise MlInternTrainingRepositoryConflict("training_capacity_exhausted")

    @_serialized_sqlite_read
    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> MlInternTrainingJobDB | None:
        with Session(self._engine) as session:
            return session.exec(self._job_query(principal).where(MlInternTrainingJobDB.id == job_id)).first()

    def get_job_by_idempotency(
        self,
        principal: MlInternTrainingPrincipal,
        idempotency_key_digest: str,
    ) -> MlInternTrainingJobDB | None:
        with Session(self._engine) as session:
            return self._job_by_idempotency(session, principal, idempotency_key_digest)

    def count_active_jobs(self) -> int:
        with Session(self._engine) as session:
            count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(
                    MlInternTrainingJobDB.status.in_(
                        {"queued", "claimed", "running", "cancel_requested", "interrupted"}
                    )
                )
            ).one()
        return int(count or 0)

    def count_executing_jobs(self) -> int:
        """Return jobs that currently consume a Hub execution slot."""

        with Session(self._engine) as session:
            count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(
                    MlInternTrainingJobDB.status.in_({"claimed", "running", "cancel_requested"})
                )
            ).one()
        return int(count or 0)

    def count_queued_jobs(self) -> int:
        with Session(self._engine) as session:
            count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(MlInternTrainingJobDB.status == "queued")
            ).one()
        return int(count or 0)

    def list_active_jobs(self, *, limit: int = 1000) -> list[MlInternTrainingJobDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(MlInternTrainingJobDB)
                    .where(
                        MlInternTrainingJobDB.status.in_(
                            {"queued", "claimed", "running", "cancel_requested", "interrupted"}
                        )
                    )
                    .order_by(MlInternTrainingJobDB.updated_at.asc())
                    .limit(max(1, min(limit, 10_000)))
                ).all()
            )

    def list_queued_jobs(self, *, limit: int = 10_000) -> list[MlInternTrainingJobDB]:
        """Return the durable global queue in deterministic arrival order."""

        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(MlInternTrainingJobDB)
                    .where(MlInternTrainingJobDB.status == "queued")
                    .order_by(MlInternTrainingJobDB.created_at.asc(), MlInternTrainingJobDB.id.asc())
                    .limit(max(1, min(limit, 10_000)))
                ).all()
            )

    def list_jobs(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        backend: str | None = None,
        dataset_id: str | None = None,
    ) -> list[MlInternTrainingJobDB]:
        statement = self._job_query(principal)
        if status:
            statement = statement.where(MlInternTrainingJobDB.status == status)
        if backend:
            statement = statement.where(MlInternTrainingJobDB.backend == backend)
        if dataset_id:
            statement = statement.where(MlInternTrainingJobDB.dataset_id == dataset_id)
        with Session(self._engine) as session:
            return list(
                session.exec(
                    statement.order_by(MlInternTrainingJobDB.created_at.desc())
                    .offset(max(0, offset))
                    .limit(max(1, min(limit, 200)))
                ).all()
            )

    def count_jobs(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        status: str | None = None,
        backend: str | None = None,
        dataset_id: str | None = None,
    ) -> int:
        statement = select(func.count(MlInternTrainingJobDB.id)).where(
            MlInternTrainingJobDB.tenant_id == principal.tenant_id,
            MlInternTrainingJobDB.owner_subject == principal.subject,
        )
        if status:
            statement = statement.where(MlInternTrainingJobDB.status == status)
        if backend:
            statement = statement.where(MlInternTrainingJobDB.backend == backend)
        if dataset_id:
            statement = statement.where(MlInternTrainingJobDB.dataset_id == dataset_id)
        with Session(self._engine) as session:
            count = session.exec(statement).one()
        return int(count or 0)

    @_serialized_write
    def save_job(self, job: MlInternTrainingJobDB, *, expected_version: int) -> MlInternTrainingJobDB:
        now = self._clock()
        values = job.model_dump(exclude={"id", "version", "created_at"})
        values.update(version=expected_version + 1, updated_at=now)
        job_event = self._job_event(
            job,
            transition="job_updated",
            reason_code=f"training_job_{job.status}",
            epoch=expected_version + 1,
        )
        with Session(self._engine) as session:
            result = session.exec(
                update(MlInternTrainingJobDB)
                .where(
                    MlInternTrainingJobDB.id == job.id,
                    MlInternTrainingJobDB.tenant_id == job.tenant_id,
                    MlInternTrainingJobDB.owner_subject == job.owner_subject,
                    MlInternTrainingJobDB.version == expected_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("job_version_conflict")
            self._enqueue(session, job_event)
            if job.status in {"cancelled", "completed", "failed"}:
                capacity = session.exec(
                    select(MlInternTrainingCapacityLeaseDB).where(
                        MlInternTrainingCapacityLeaseDB.job_id == job.id
                    )
                ).first()
                execution = session.exec(
                    select(MlInternTrainingExecutionLeaseDB).where(
                        MlInternTrainingExecutionLeaseDB.job_id == job.id
                    )
                ).first()
                if capacity is not None:
                    self._enqueue(
                        session,
                        self._capacity_event(
                            job,
                            capacity,
                            transition="capacity_released",
                            reason_code="training_job_terminal",
                            epoch=capacity.version + 1,
                        ),
                    )
                    session.delete(capacity)
                if execution is not None:
                    self._enqueue(
                        session,
                        self._execution_event(
                            job,
                            execution,
                            transition="execution_released",
                            reason_code="training_job_terminal",
                            epoch=execution.version + 1,
                        ),
                    )
                    session.delete(execution)
            session.commit()
        saved = self.get_job(MlInternTrainingPrincipal(job.tenant_id, job.owner_subject), job.id)
        if saved is None:
            raise MlInternTrainingRepositoryConflict("job_disappeared")
        return saved

    @_serialized_write
    def fence_by_request_digest(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        request_digest: str,
        revocation_epoch: int,
    ) -> bool:
        """Fence lineage-bound jobs and active attempts in one audited transaction."""

        if revocation_epoch < 1:
            raise ValueError("revocation_epoch_invalid")
        now = self._clock()
        with Session(self._engine) as session:
            jobs = session.exec(
                self._job_query(principal)
                .where(MlInternTrainingJobDB.request_digest == request_digest)
                .with_for_update()
            ).all()
            for job in jobs:
                if job.error_code == "speech_evidence_revoked":
                    self._enqueue(
                        session,
                        self._job_event(
                            job,
                            transition="job_fenced",
                            reason_code="speech_evidence_revoked",
                            epoch=job.version,
                        ),
                    )
                    continue
                if job.status == "queued":
                    job.status = "cancelled"
                    job.phase = "cancelled"
                    job.finished_at = now
                elif job.status in {
                    "claimed",
                    "running",
                    "cancel_requested",
                    "interrupted",
                }:
                    job.status = "cancel_requested"
                    job.cancel_requested = True
                job.error_code = "speech_evidence_revoked"
                job.version += 1
                job.updated_at = now
                session.add(job)
                self._enqueue(
                    session,
                    self._job_event(
                        job,
                        transition="job_fenced",
                        reason_code="speech_evidence_revoked",
                        epoch=job.version,
                    ),
                )
                attempts = session.exec(
                    select(MlInternTrainingAttemptDB)
                    .where(
                        MlInternTrainingAttemptDB.job_id == job.id,
                        MlInternTrainingAttemptDB.status.in_(["claimed", "running"]),
                    )
                    .with_for_update()
                ).all()
                for attempt in attempts:
                    attempt.status = "fenced"
                    attempt.error_code = "speech_evidence_revoked"
                    attempt.version += 1
                    attempt.updated_at = now
                    session.add(attempt)
                    self._enqueue(
                        session,
                        self._attempt_event(
                            attempt,
                            transition="attempt_fenced",
                            reason_code="speech_evidence_revoked",
                            epoch=attempt.version,
                        ),
                    )
                if job.status == "cancelled":
                    capacity = session.exec(
                        select(MlInternTrainingCapacityLeaseDB).where(
                            MlInternTrainingCapacityLeaseDB.job_id == job.id
                        )
                    ).first()
                    execution = session.exec(
                        select(MlInternTrainingExecutionLeaseDB).where(
                            MlInternTrainingExecutionLeaseDB.job_id == job.id
                        )
                    ).first()
                    if capacity is not None:
                        self._enqueue(
                            session,
                            self._capacity_event(
                                job,
                                capacity,
                                transition="capacity_released",
                                reason_code="speech_evidence_revoked",
                                epoch=capacity.version + 1,
                            ),
                        )
                        session.delete(capacity)
                    if execution is not None:
                        self._enqueue(
                            session,
                            self._execution_event(
                                job,
                                execution,
                                transition="execution_released",
                                reason_code="speech_evidence_revoked",
                                epoch=execution.version + 1,
                            ),
                        )
                        session.delete(execution)
            session.commit()
            return bool(jobs)

    @_serialized_write
    def try_acquire_execution_slot(
        self,
        job_id: str,
        *,
        limit: int,
        lease_expires_at: float,
        now: float | None = None,
    ) -> int | None:
        """Acquire one cluster-wide execution slot without count-then-insert races."""

        if not 1 <= limit <= 128:
            raise ValueError("execution capacity is outside its bounds")
        timestamp = self._clock() if now is None else float(now)
        with Session(self._engine) as session:
            expired = session.exec(
                select(MlInternTrainingExecutionLeaseDB).where(
                    MlInternTrainingExecutionLeaseDB.lease_expires_at <= timestamp
                )
            ).all()
            for lease in expired:
                expired_job = session.get(MlInternTrainingJobDB, lease.job_id)
                if expired_job is not None:
                    self._enqueue(
                        session,
                        self._execution_event(
                            expired_job,
                            lease,
                            transition="execution_expired",
                            reason_code="training_execution_lease_expired",
                            epoch=lease.version + 1,
                        ),
                    )
                session.delete(lease)
            existing = session.exec(
                select(MlInternTrainingExecutionLeaseDB).where(
                    MlInternTrainingExecutionLeaseDB.job_id == job_id
                )
            ).first()
            if existing is not None:
                job = session.get(MlInternTrainingJobDB, job_id)
                if job is not None:
                    self._enqueue(
                        session,
                        self._execution_event(
                            job,
                            existing,
                            transition="execution_acquired",
                            reason_code="training_execution_acquired",
                            epoch=existing.version,
                        ),
                    )
                session.commit()
                # A second Hub replica must not join an already-running
                # execution for the same job. Only an expired lease is
                # reclaimable; those were deleted above.
                return None
            session.commit()
        for slot in range(limit):
            with Session(self._engine) as session:
                job = session.get(MlInternTrainingJobDB, job_id)
                if job is None:
                    raise KeyError(job_id)
                lease = MlInternTrainingExecutionLeaseDB(
                    slot=slot,
                    job_id=job_id,
                    lease_expires_at=lease_expires_at,
                )
                session.add(lease)
                try:
                    with session.no_autoflush:
                        self._enqueue(
                            session,
                            self._execution_event(
                                job,
                                lease,
                                transition="execution_acquired",
                                reason_code="training_execution_acquired",
                                epoch=lease.version,
                            ),
                        )
                    session.commit()
                    return slot
                except IntegrityError:
                    session.rollback()
                    existing = session.exec(
                        select(MlInternTrainingExecutionLeaseDB).where(
                            MlInternTrainingExecutionLeaseDB.job_id == job_id
                        )
                    ).first()
                    if existing is not None:
                        self._enqueue(
                            session,
                            self._execution_event(
                                job,
                                existing,
                                transition="execution_acquired",
                                reason_code="training_execution_acquired",
                                epoch=existing.version,
                            ),
                        )
                        session.commit()
                        return None
        return None

    @_serialized_write
    def renew_execution_slot(self, job_id: str, *, lease_expires_at: float) -> bool:
        with Session(self._engine) as session:
            lease = session.exec(
                select(MlInternTrainingExecutionLeaseDB)
                .where(MlInternTrainingExecutionLeaseDB.job_id == job_id)
                .with_for_update()
            ).first()
            if lease is None:
                return False
            job = session.get(MlInternTrainingJobDB, job_id)
            if job is None:
                raise KeyError(job_id)
            next_version = lease.version + 1
            result = session.exec(
                update(MlInternTrainingExecutionLeaseDB)
                .where(
                    MlInternTrainingExecutionLeaseDB.job_id == job_id,
                    MlInternTrainingExecutionLeaseDB.version == lease.version,
                )
                .values(
                    lease_expires_at=lease_expires_at,
                    version=next_version,
                    updated_at=self._clock(),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("execution_lease_version_conflict")
            self._enqueue(
                session,
                self._execution_event(
                    job,
                    lease,
                    transition="execution_renewed",
                    reason_code="training_execution_renewed",
                    epoch=next_version,
                ),
            )
            session.commit()
            return True

    @_serialized_write
    def release_execution_slot(self, job_id: str) -> None:
        with Session(self._engine) as session:
            lease = session.exec(
                select(MlInternTrainingExecutionLeaseDB)
                .where(MlInternTrainingExecutionLeaseDB.job_id == job_id)
                .with_for_update()
            ).first()
            if lease is None:
                return
            job = session.get(MlInternTrainingJobDB, job_id)
            if job is None:
                raise KeyError(job_id)
            self._enqueue(
                session,
                self._execution_event(
                    job,
                    lease,
                    transition="execution_released",
                    reason_code="training_execution_released",
                    epoch=lease.version + 1,
                ),
            )
            session.delete(lease)
            session.commit()

    @_serialized_write
    def append_event(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        *,
        event_type: str,
        dedupe_key: str,
        payload: dict,
    ) -> MlInternTrainingEventDB:
        for _attempt in range(3):
            with Session(self._engine) as session:
                job = session.exec(self._job_query(principal).where(MlInternTrainingJobDB.id == job_id)).first()
                if job is None:
                    raise KeyError(job_id)
                existing = session.exec(
                    select(MlInternTrainingEventDB).where(
                        MlInternTrainingEventDB.job_id == job_id,
                        MlInternTrainingEventDB.dedupe_key == dedupe_key,
                    )
                ).first()
                if existing is not None:
                    self._enqueue(
                        session,
                        self._training_event_event(job, existing),
                    )
                    session.commit()
                    session.refresh(existing)
                    session.expunge(existing)
                    return existing
                last_sequence = session.exec(
                    select(func.max(MlInternTrainingEventDB.sequence)).where(MlInternTrainingEventDB.job_id == job_id)
                ).one()
                event = MlInternTrainingEventDB(
                    job_id=job_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    sequence=int(last_sequence or 0) + 1,
                    event_type=event_type,
                    dedupe_key=dedupe_key,
                    payload=dict(payload),
                )
                session.add(event)
                try:
                    with session.no_autoflush:
                        self._enqueue(session, self._training_event_event(job, event))
                    session.commit()
                    session.refresh(event)
                    return event
                except IntegrityError:
                    session.rollback()
        raise MlInternTrainingRepositoryConflict("event_sequence_conflict")

    def list_events(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[MlInternTrainingEventDB]:
        with Session(self._engine) as session:
            job = session.exec(self._job_query(principal).where(MlInternTrainingJobDB.id == job_id)).first()
            if job is None:
                raise KeyError(job_id)
            return list(
                session.exec(
                    select(MlInternTrainingEventDB)
                    .where(
                        MlInternTrainingEventDB.job_id == job_id,
                        MlInternTrainingEventDB.tenant_id == principal.tenant_id,
                        MlInternTrainingEventDB.owner_subject == principal.subject,
                        MlInternTrainingEventDB.sequence > max(0, after_sequence),
                    )
                    .order_by(MlInternTrainingEventDB.sequence.asc())
                    .limit(max(1, min(limit, 500)))
                ).all()
            )

    @_serialized_write
    def create_attempt(self, attempt: MlInternTrainingAttemptDB) -> MlInternTrainingAttemptDB:
        with Session(self._engine) as session:
            existing = session.exec(
                select(MlInternTrainingAttemptDB).where(
                    MlInternTrainingAttemptDB.job_id == attempt.job_id,
                    MlInternTrainingAttemptDB.attempt_number == attempt.attempt_number,
                )
            ).first()
            if existing is not None:
                if existing.id != attempt.id or existing.fencing_token_digest != attempt.fencing_token_digest:
                    raise MlInternTrainingRepositoryConflict("attempt_number_conflict")
                self._enqueue(
                    session,
                    self._attempt_event(
                        existing,
                        transition="attempt_created",
                        reason_code="training_attempt_created",
                        epoch=existing.version,
                    ),
                )
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            session.add(attempt)
            try:
                with session.no_autoflush:
                    self._enqueue(
                        session,
                        self._attempt_event(
                            attempt,
                            transition="attempt_created",
                            reason_code="training_attempt_created",
                            epoch=attempt.version,
                        ),
                    )
                session.commit()
                session.refresh(attempt)
                return attempt
            except IntegrityError as exc:
                session.rollback()
                existing = session.exec(
                    select(MlInternTrainingAttemptDB).where(
                        MlInternTrainingAttemptDB.job_id == attempt.job_id,
                        MlInternTrainingAttemptDB.attempt_number == attempt.attempt_number,
                    )
                ).first()
                if (
                    existing is None
                    or existing.id != attempt.id
                    or existing.fencing_token_digest != attempt.fencing_token_digest
                ):
                    raise MlInternTrainingRepositoryConflict("attempt_number_conflict") from exc
                self._enqueue(
                    session,
                    self._attempt_event(
                        existing,
                        transition="attempt_created",
                        reason_code="training_attempt_created",
                        epoch=existing.version,
                    ),
                )
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing

    def get_attempt(self, attempt_id: str) -> MlInternTrainingAttemptDB | None:
        with Session(self._engine) as session:
            return session.get(MlInternTrainingAttemptDB, attempt_id)

    def list_attempts(self, job_id: str, *, limit: int = 100) -> list[MlInternTrainingAttemptDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(MlInternTrainingAttemptDB)
                    .where(MlInternTrainingAttemptDB.job_id == job_id)
                    .order_by(MlInternTrainingAttemptDB.attempt_number.desc())
                    .limit(max(1, min(limit, 1000)))
                ).all()
            )

    def next_attempt_number(self, job_id: str) -> int:
        with Session(self._engine) as session:
            latest = session.exec(
                select(func.max(MlInternTrainingAttemptDB.attempt_number)).where(
                    MlInternTrainingAttemptDB.job_id == job_id
                )
            ).one()
        return int(latest or 0) + 1

    @_serialized_write
    def save_attempt(
        self,
        attempt: MlInternTrainingAttemptDB,
        *,
        expected_version: int,
    ) -> MlInternTrainingAttemptDB:
        values = attempt.model_dump(exclude={"id", "version", "created_at"})
        values.update(version=expected_version + 1, updated_at=self._clock())
        audit_event = self._attempt_event(
            attempt,
            transition="attempt_updated",
            reason_code=f"training_attempt_{attempt.status}",
            epoch=expected_version + 1,
        )
        with Session(self._engine) as session:
            result = session.exec(
                update(MlInternTrainingAttemptDB)
                .where(
                    MlInternTrainingAttemptDB.id == attempt.id,
                    MlInternTrainingAttemptDB.tenant_id == attempt.tenant_id,
                    MlInternTrainingAttemptDB.owner_subject == attempt.owner_subject,
                    MlInternTrainingAttemptDB.version == expected_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("attempt_version_conflict")
            self._enqueue(session, audit_event)
            session.commit()
            saved = session.get(MlInternTrainingAttemptDB, attempt.id)
            if saved is None:
                raise MlInternTrainingRepositoryConflict("attempt_disappeared")
            session.expunge(saved)
            return saved

    def _dataset_event(
        self,
        dataset: MlInternDatasetDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self._prepare(
            tenant_id=dataset.tenant_id,
            scope=f"ml-training-dataset:{dataset.owner_subject}:{dataset.id}",
            event_type="speech_dataset",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=dataset.id,
            idempotency_key=f"ml-training:{transition}:{dataset.id}:{epoch}",
        )

    def _job_event(
        self,
        job: MlInternTrainingJobDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self._prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=job.id,
            idempotency_key=f"ml-training:{transition}:{job.id}:{epoch}",
        )

    def _capacity_event(
        self,
        job: MlInternTrainingJobDB,
        lease: MlInternTrainingCapacityLeaseDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self._prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=job.id,
            lease_ref=f"training-capacity:{lease.slot}:{job.id}",
            idempotency_key=f"ml-training:{transition}:{job.id}:{lease.slot}:{epoch}",
        )

    def _execution_event(
        self,
        job: MlInternTrainingJobDB,
        lease: MlInternTrainingExecutionLeaseDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        # Execution leases are deliberately deleted and may later be acquired
        # again in the same numeric slot.  Version therefore is only an epoch
        # within one lease generation and cannot by itself identify the
        # authority transition.  Bind the audit idempotency key to the stable
        # creation value so replays of one lease collapse while reacquisitions
        # remain distinct.
        generation = hashlib.sha256(
            f"{lease.job_id}:{lease.slot}:{float(lease.created_at).hex()}".encode()
        ).hexdigest()[:24]
        return self._prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=job.id,
            lease_ref=f"training-execution:{lease.slot}:{job.id}",
            idempotency_key=(
                f"ml-training:{transition}:{job.id}:{lease.slot}:{generation}:{epoch}"
            ),
        )

    def _attempt_event(
        self,
        attempt: MlInternTrainingAttemptDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self._prepare(
            tenant_id=attempt.tenant_id,
            scope=f"ml-training-job:{attempt.owner_subject}:{attempt.job_id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=attempt.id,
            idempotency_key=f"ml-training:{transition}:{attempt.id}:{epoch}",
        )

    def _training_event_event(
        self,
        job: MlInternTrainingJobDB,
        event: MlInternTrainingEventDB,
    ) -> SemanticMediaAuditEvent | None:
        dedupe_digest = hashlib.sha256(event.dedupe_key.encode("utf-8")).hexdigest()
        return self._prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition="event_appended",
            reason_code="training_event_appended",
            epoch=event.sequence,
            job_ref=event.id,
            idempotency_key=f"ml-training:event:{job.id}:{dedupe_digest}",
        )

    def _enqueue_job_and_capacity_replay(
        self,
        session: Session,
        job: MlInternTrainingJobDB,
    ) -> None:
        self._enqueue(
            session,
            self._job_event(
                job,
                transition="job_created",
                reason_code="training_job_created",
                epoch=1,
            ),
        )
        lease = session.exec(
            select(MlInternTrainingCapacityLeaseDB).where(
                MlInternTrainingCapacityLeaseDB.job_id == job.id
            )
        ).first()
        if lease is not None:
            self._enqueue(
                session,
                self._capacity_event(
                    job,
                    lease,
                    transition="capacity_acquired",
                    reason_code="training_capacity_acquired",
                    epoch=lease.version,
                ),
            )

    def _prepare(
        self,
        *,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        idempotency_key: str,
        job_ref: str | None = None,
        lease_ref: str | None = None,
    ) -> SemanticMediaAuditEvent | None:
        audit = self._audit or _DEFAULT_AUDIT
        if audit is None:
            return None
        return audit.prepare_transition(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            scope=scope,
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=max(1, int(epoch)),
            job_ref=job_ref,
            lease_ref=lease_ref,
        )

    @staticmethod
    def _enqueue(session: Session, event: SemanticMediaAuditEvent | None) -> None:
        if event is not None:
            SqlSemanticMediaAuditOutbox.enqueue_in_session(session, event)

    @staticmethod
    def _dataset_query(principal: MlInternTrainingPrincipal):
        return select(MlInternDatasetDB).where(
            MlInternDatasetDB.tenant_id == principal.tenant_id,
            MlInternDatasetDB.owner_subject == principal.subject,
        )

    @staticmethod
    def _job_query(principal: MlInternTrainingPrincipal):
        return select(MlInternTrainingJobDB).where(
            MlInternTrainingJobDB.tenant_id == principal.tenant_id,
            MlInternTrainingJobDB.owner_subject == principal.subject,
        )

    @staticmethod
    def _job_by_idempotency(session: Session, principal: MlInternTrainingPrincipal, digest: str):
        return session.exec(
            MlInternTrainingRepository._job_query(principal).where(
                MlInternTrainingJobDB.idempotency_key_digest == digest
            )
        ).first()


_repository: MlInternTrainingRepository | None = None


def configure_ml_intern_training_audit(audit: SemanticMediaAuditPort | None) -> None:
    """Set the production audit recorder used by Hub training repositories."""

    global _DEFAULT_AUDIT
    _DEFAULT_AUDIT = audit


def get_ml_intern_training_repository() -> MlInternTrainingRepository:
    global _repository
    if _repository is None:
        _repository = MlInternTrainingRepository()
    return _repository


__all__ = [
    "MlInternTrainingRepository",
    "MlInternTrainingRepositoryConflict",
    "configure_ml_intern_training_audit",
    "get_ml_intern_training_repository",
]
