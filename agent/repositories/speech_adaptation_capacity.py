"""SQL capacity-lease port for Hub-owned speech-adaptation training slots."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models.speech_adaptation import SpeechAdaptationCapacityLeaseDB
from agent.models.speech_adaptation_admission import SpeechCapacityLease
from agent.repositories.speech_adaptation_storage import SPEECH_ADAPTATION_WRITE_LOCK as _WRITE_LOCK


class SqlSpeechAdaptationCapacityLeasePort:
    """Atomic SQL slot acquisition shared by all Hub replicas."""

    def __init__(self, *, capacity: int = 1, lease_seconds: int = 300) -> None:
        if not 1 <= capacity <= 128 or not 10 <= lease_seconds <= 3600:
            raise ValueError("speech capacity configuration is invalid")
        self._capacity = capacity
        self._lease_ms = lease_seconds * 1000

    def try_acquire(self, *, job_id: str, deadline_at_ms: int, now_ms: int) -> SpeechCapacityLease | None:
        expires = min(deadline_at_ms, now_ms + self._lease_ms)
        if expires <= now_ms:
            return None
        with _WRITE_LOCK:
            with Session(engine) as session:
                session.exec(
                    delete(SpeechAdaptationCapacityLeaseDB).where(
                        SpeechAdaptationCapacityLeaseDB.expires_at_ms <= now_ms
                    )
                )
                existing = session.exec(
                    select(SpeechAdaptationCapacityLeaseDB).where(SpeechAdaptationCapacityLeaseDB.job_id == job_id)
                ).first()
                session.commit()
                if existing is not None:
                    return _lease(existing)
            for slot in range(self._capacity):
                for _attempt in range(4):
                    # Audit/debug contracts are shared with TypeScript and
                    # therefore keep epochs inside the exact signed-int32
                    # range. Randomness still provides ample collision space
                    # for the bounded (<=128) active lease set.
                    epoch = secrets.randbelow(2**31 - 1) + 1
                    lease_id = f"speech-lease-{hashlib.sha256(f'{job_id}:{epoch}'.encode()).hexdigest()[:32]}"
                    row = SpeechAdaptationCapacityLeaseDB(
                        slot=slot,
                        job_id=job_id,
                        lease_id=lease_id,
                        epoch=epoch,
                        expires_at_ms=expires,
                        created_at_ms=now_ms,
                    )
                    with Session(engine) as session:
                        session.add(row)
                        try:
                            session.commit()
                            return _lease(row)
                        except IntegrityError:
                            session.rollback()
                            existing = session.exec(
                                select(SpeechAdaptationCapacityLeaseDB).where(
                                    SpeechAdaptationCapacityLeaseDB.job_id == job_id
                                )
                            ).first()
                            if existing is not None:
                                return _lease(existing)
                # Another Hub owns this slot; continue to the next one.
            return None

    def release(self, lease_id: str) -> None:
        with _WRITE_LOCK:
            with Session(engine) as session:
                session.exec(
                    delete(SpeechAdaptationCapacityLeaseDB).where(SpeechAdaptationCapacityLeaseDB.lease_id == lease_id)
                )
                session.commit()


def _lease(row: SpeechAdaptationCapacityLeaseDB) -> SpeechCapacityLease:
    return SpeechCapacityLease(row.lease_id, row.epoch, row.expires_at_ms)


__all__ = ["SqlSpeechAdaptationCapacityLeasePort"]
