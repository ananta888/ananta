"""Cluster-wide execution-slot leases for ML-Intern training jobs.

The training repository stays the public, serialized persistence entry point
and delegates the execution-lease lifecycle (acquire, renew, release) to this
store (SRP). Engine, clock, audit-event factory and the transactional outbox
enqueue are injected by the repository (DIP), so every lease transition is
still audited inside its own domain transaction.
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, update

from agent.db_models import MlInternTrainingExecutionLeaseDB, MlInternTrainingJobDB
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.repositories.ml_intern_training_audit_events import MlInternTrainingAuditEvents
from agent.repositories.ml_intern_training_conflict import MlInternTrainingRepositoryConflict


class MlInternTrainingExecutionLeaseStore:
    """Acquire, renew and release execution slots without count-then-insert races."""

    def __init__(
        self,
        *,
        db_engine,
        events: MlInternTrainingAuditEvents,
        enqueue: Callable[[Session, SemanticMediaAuditEvent | None], None],
        clock: Callable[[], float],
    ) -> None:
        self._engine = db_engine
        self._events = events
        self._enqueue = enqueue
        self._clock = clock

    def try_acquire(
        self,
        job_id: str,
        *,
        limit: int,
        lease_expires_at: float,
        now: float | None = None,
    ) -> int | None:
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
                        self._events.execution(
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
                        self._events.execution(
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
                            self._events.execution(
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
                            self._events.execution(
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

    def renew(self, job_id: str, *, lease_expires_at: float) -> bool:
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
                self._events.execution(
                    job,
                    lease,
                    transition="execution_renewed",
                    reason_code="training_execution_renewed",
                    epoch=next_version,
                ),
            )
            session.commit()
            return True

    def release(self, job_id: str) -> None:
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
                self._events.execution(
                    job,
                    lease,
                    transition="execution_released",
                    reason_code="training_execution_released",
                    epoch=lease.version + 1,
                ),
            )
            session.delete(lease)
            session.commit()


__all__ = ["MlInternTrainingExecutionLeaseStore"]
