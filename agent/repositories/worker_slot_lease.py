from __future__ import annotations

import time
from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import WorkerSlotLeaseDB


class WorkerSlotLeaseRepository:
    def get_by_id(self, lease_id: str) -> WorkerSlotLeaseDB | None:
        with Session(engine) as session:
            return session.get(WorkerSlotLeaseDB, lease_id)

    def save(self, lease: WorkerSlotLeaseDB) -> WorkerSlotLeaseDB:
        with Session(engine) as session:
            session.add(lease)
            session.commit()
            session.refresh(lease)
            return lease

    def list_active(self) -> List[WorkerSlotLeaseDB]:
        with Session(engine) as session:
            stmt = select(WorkerSlotLeaseDB).where(WorkerSlotLeaseDB.status == "active").order_by(WorkerSlotLeaseDB.acquired_at.asc())
            return session.exec(stmt).all()

    def list_all(self) -> List[WorkerSlotLeaseDB]:
        with Session(engine) as session:
            stmt = select(WorkerSlotLeaseDB).order_by(WorkerSlotLeaseDB.acquired_at.asc())
            return session.exec(stmt).all()

    def list_queued(self) -> List[WorkerSlotLeaseDB]:
        with Session(engine) as session:
            stmt = select(WorkerSlotLeaseDB).where(WorkerSlotLeaseDB.status == "queued").order_by(WorkerSlotLeaseDB.acquired_at.asc())
            return session.exec(stmt).all()

    def list_rejected(self) -> List[WorkerSlotLeaseDB]:
        with Session(engine) as session:
            stmt = select(WorkerSlotLeaseDB).where(WorkerSlotLeaseDB.status == "rejected").order_by(WorkerSlotLeaseDB.acquired_at.asc())
            return session.exec(stmt).all()

    def list_expired(self, now_ts: float | None = None) -> List[WorkerSlotLeaseDB]:
        now_value = float(now_ts or time.time())
        with Session(engine) as session:
            stmt = select(WorkerSlotLeaseDB).where(
                WorkerSlotLeaseDB.status.in_(["active", "queued"]),
                WorkerSlotLeaseDB.deadline_at < now_value,
            )
            return session.exec(stmt).all()

    def mark_rejected(self, lease_id: str, reason_code: str) -> WorkerSlotLeaseDB | None:
        lease = self.get_by_id(lease_id)
        if lease is None:
            return None
        lease.status = "rejected"
        lease.reason_code = reason_code
        lease.released_at = time.time()
        return self.save(lease)

    def release(self, lease_id: str, *, status: str = "released") -> WorkerSlotLeaseDB | None:
        lease = self.get_by_id(lease_id)
        if lease is None:
            return None
        if lease.status in {"released", "rejected", "stale_released"}:
            return lease
        lease.status = status
        lease.released_at = time.time()
        return self.save(lease)
