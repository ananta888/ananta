from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, func, select, update

from agent.database import engine
from agent.db_models import (
    MlInternDatasetDB,
    MlInternTrainingAttemptDB,
    MlInternTrainingCapacityLeaseDB,
    MlInternTrainingEventDB,
    MlInternTrainingExecutionLeaseDB,
    MlInternTrainingJobDB,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal

_P = ParamSpec("_P")
_R = TypeVar("_R")
_REPOSITORY_WRITE_LOCK = threading.RLock()


def _serialized_write(callback: Callable[_P, _R]) -> Callable[_P, _R]:
    """Prevent transaction overlap on one process-local SQLite connection.

    Database constraints remain authoritative between Hub processes. This lock
    also makes the in-memory SQLite ``StaticPool`` test adapter safe, because
    that adapter intentionally shares one DBAPI connection across threads.
    """

    @wraps(callback)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _REPOSITORY_WRITE_LOCK:
            return callback(*args, **kwargs)

    return guarded


class MlInternTrainingRepositoryConflict(RuntimeError):
    pass


class MlInternTrainingRepository:
    """SQL-backed, tenant-scoped persistence adapter for training control state."""

    @_serialized_write
    def create_dataset(self, dataset: MlInternDatasetDB) -> tuple[MlInternDatasetDB, bool]:
        with Session(engine) as session:
            existing = session.exec(
                select(MlInternDatasetDB).where(
                    MlInternDatasetDB.tenant_id == dataset.tenant_id,
                    MlInternDatasetDB.owner_subject == dataset.owner_subject,
                    MlInternDatasetDB.content_sha256 == dataset.content_sha256,
                )
            ).first()
            if existing is not None:
                return existing, True
            session.add(dataset)
            try:
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
                return existing, True

    def get_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> MlInternDatasetDB | None:
        with Session(engine) as session:
            return session.exec(self._dataset_query(principal).where(MlInternDatasetDB.id == dataset_id)).first()

    def list_datasets(
        self, principal: MlInternTrainingPrincipal, *, limit: int, offset: int
    ) -> list[MlInternDatasetDB]:
        with Session(engine) as session:
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
        now = time.time()
        values = dataset.model_dump(exclude={"id", "version", "created_at"})
        values.update(version=expected_version + 1, updated_at=now)
        with Session(engine) as session:
            result = session.exec(
                update(MlInternDatasetDB)
                .where(MlInternDatasetDB.id == dataset.id, MlInternDatasetDB.version == expected_version)
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("dataset_version_conflict")
            session.commit()
        saved = self.get_dataset(MlInternTrainingPrincipal(dataset.tenant_id, dataset.owner_subject), dataset.id)
        if saved is None:
            raise MlInternTrainingRepositoryConflict("dataset_disappeared")
        return saved

    @_serialized_write
    def delete_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> bool:
        with Session(engine) as session:
            dataset = session.exec(self._dataset_query(principal).where(MlInternDatasetDB.id == dataset_id)).first()
            if dataset is None:
                return False
            reference_count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(MlInternTrainingJobDB.dataset_id == dataset_id)
            ).one()
            if int(reference_count or 0):
                raise MlInternTrainingRepositoryConflict("dataset_referenced")
            session.delete(dataset)
            session.commit()
            return True

    @_serialized_write
    def create_job(self, job: MlInternTrainingJobDB) -> tuple[MlInternTrainingJobDB, bool]:
        principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
        with Session(engine) as session:
            existing = self._job_by_idempotency(session, principal, job.idempotency_key_digest)
            if existing is not None:
                if existing.request_digest != job.request_digest:
                    raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                return existing, True
            session.add(job)
            try:
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
            with Session(engine) as session:
                existing = self._job_by_idempotency(session, principal, job.idempotency_key_digest)
                if existing is not None:
                    if existing.request_digest != job.request_digest:
                        raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                    return existing, True
                candidate = MlInternTrainingJobDB.model_validate(job.model_dump())
                session.add(candidate)
                session.add(MlInternTrainingCapacityLeaseDB(slot=slot, job_id=candidate.id))
                try:
                    session.commit()
                    session.refresh(candidate)
                    return candidate, False
                except IntegrityError:
                    session.rollback()
                    existing = self._job_by_idempotency(
                        session,
                        principal,
                        job.idempotency_key_digest,
                    )
                    if existing is not None:
                        if existing.request_digest != job.request_digest:
                            raise MlInternTrainingRepositoryConflict("idempotency_payload_conflict")
                        return existing, True
        raise MlInternTrainingRepositoryConflict("training_capacity_exhausted")

    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> MlInternTrainingJobDB | None:
        with Session(engine) as session:
            return session.exec(self._job_query(principal).where(MlInternTrainingJobDB.id == job_id)).first()

    def get_job_by_idempotency(
        self,
        principal: MlInternTrainingPrincipal,
        idempotency_key_digest: str,
    ) -> MlInternTrainingJobDB | None:
        with Session(engine) as session:
            return self._job_by_idempotency(session, principal, idempotency_key_digest)

    def count_active_jobs(self) -> int:
        with Session(engine) as session:
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

        with Session(engine) as session:
            count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(
                    MlInternTrainingJobDB.status.in_({"claimed", "running", "cancel_requested"})
                )
            ).one()
        return int(count or 0)

    def count_queued_jobs(self) -> int:
        with Session(engine) as session:
            count = session.exec(
                select(func.count(MlInternTrainingJobDB.id)).where(MlInternTrainingJobDB.status == "queued")
            ).one()
        return int(count or 0)

    def list_active_jobs(self, *, limit: int = 1000) -> list[MlInternTrainingJobDB]:
        with Session(engine) as session:
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

        with Session(engine) as session:
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
        with Session(engine) as session:
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
        with Session(engine) as session:
            count = session.exec(statement).one()
        return int(count or 0)

    @_serialized_write
    def save_job(self, job: MlInternTrainingJobDB, *, expected_version: int) -> MlInternTrainingJobDB:
        now = time.time()
        values = job.model_dump(exclude={"id", "version", "created_at"})
        values.update(version=expected_version + 1, updated_at=now)
        with Session(engine) as session:
            result = session.exec(
                update(MlInternTrainingJobDB)
                .where(MlInternTrainingJobDB.id == job.id, MlInternTrainingJobDB.version == expected_version)
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("job_version_conflict")
            if job.status in {"cancelled", "completed", "failed"}:
                session.exec(
                    delete(MlInternTrainingCapacityLeaseDB).where(
                        MlInternTrainingCapacityLeaseDB.job_id == job.id
                    )
                )
                session.exec(
                    delete(MlInternTrainingExecutionLeaseDB).where(
                        MlInternTrainingExecutionLeaseDB.job_id == job.id
                    )
                )
            session.commit()
        saved = self.get_job(MlInternTrainingPrincipal(job.tenant_id, job.owner_subject), job.id)
        if saved is None:
            raise MlInternTrainingRepositoryConflict("job_disappeared")
        return saved

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
        timestamp = time.time() if now is None else float(now)
        with Session(engine) as session:
            session.exec(
                delete(MlInternTrainingExecutionLeaseDB).where(
                    MlInternTrainingExecutionLeaseDB.lease_expires_at <= timestamp
                )
            )
            existing = session.exec(
                select(MlInternTrainingExecutionLeaseDB).where(
                    MlInternTrainingExecutionLeaseDB.job_id == job_id
                )
            ).first()
            session.commit()
            if existing is not None:
                # A second Hub replica must not join an already-running
                # execution for the same job. Only an expired lease is
                # reclaimable; those were deleted above.
                return None
        for slot in range(limit):
            with Session(engine) as session:
                session.add(
                    MlInternTrainingExecutionLeaseDB(
                        slot=slot,
                        job_id=job_id,
                        lease_expires_at=lease_expires_at,
                    )
                )
                try:
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
                        return None
        return None

    @_serialized_write
    def renew_execution_slot(self, job_id: str, *, lease_expires_at: float) -> bool:
        with Session(engine) as session:
            result = session.exec(
                update(MlInternTrainingExecutionLeaseDB)
                .where(MlInternTrainingExecutionLeaseDB.job_id == job_id)
                .values(lease_expires_at=lease_expires_at, updated_at=time.time())
            )
            session.commit()
            return result.rowcount == 1

    @_serialized_write
    def release_execution_slot(self, job_id: str) -> None:
        with Session(engine) as session:
            session.exec(
                delete(MlInternTrainingExecutionLeaseDB).where(
                    MlInternTrainingExecutionLeaseDB.job_id == job_id
                )
            )
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
            with Session(engine) as session:
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
        with Session(engine) as session:
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
        with Session(engine) as session:
            session.add(attempt)
            try:
                session.commit()
                session.refresh(attempt)
                return attempt
            except IntegrityError as exc:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("attempt_number_conflict") from exc

    def get_attempt(self, attempt_id: str) -> MlInternTrainingAttemptDB | None:
        with Session(engine) as session:
            return session.get(MlInternTrainingAttemptDB, attempt_id)

    def list_attempts(self, job_id: str, *, limit: int = 100) -> list[MlInternTrainingAttemptDB]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(MlInternTrainingAttemptDB)
                    .where(MlInternTrainingAttemptDB.job_id == job_id)
                    .order_by(MlInternTrainingAttemptDB.attempt_number.desc())
                    .limit(max(1, min(limit, 1000)))
                ).all()
            )

    def next_attempt_number(self, job_id: str) -> int:
        with Session(engine) as session:
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
        values.update(version=expected_version + 1, updated_at=time.time())
        with Session(engine) as session:
            result = session.exec(
                update(MlInternTrainingAttemptDB)
                .where(
                    MlInternTrainingAttemptDB.id == attempt.id,
                    MlInternTrainingAttemptDB.version == expected_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MlInternTrainingRepositoryConflict("attempt_version_conflict")
            session.commit()
            saved = session.get(MlInternTrainingAttemptDB, attempt.id)
            if saved is None:
                raise MlInternTrainingRepositoryConflict("attempt_disappeared")
            session.expunge(saved)
            return saved

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


def get_ml_intern_training_repository() -> MlInternTrainingRepository:
    global _repository
    if _repository is None:
        _repository = MlInternTrainingRepository()
    return _repository
