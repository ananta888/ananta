"""HeuristicLeaseRepository — acquire, release, query HeuristicDecisionLeases.

TTL semantics:
  snake domains:  5–10 s  (default 7 s)
  chat domains:   10–20 s (default 15 s)

Lease lifecycle: active → expired | superseded | released
"""
from __future__ import annotations

import time
import uuid
from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import HeuristicDecisionLeaseDB

_DOMAIN_TTL_DEFAULTS: dict[str, float] = {
    "tui_snake": 7.0,
    "eclipse_snake": 7.0,
    "chat_codecompass": 15.0,
}


class HeuristicLeaseRepository:
    def get_by_id(self, lease_id: str) -> HeuristicDecisionLeaseDB | None:
        with Session(engine) as session:
            return session.get(HeuristicDecisionLeaseDB, lease_id)

    def save(self, lease: HeuristicDecisionLeaseDB) -> HeuristicDecisionLeaseDB:
        with Session(engine) as session:
            session.add(lease)
            session.commit()
            session.refresh(lease)
            return lease

    def acquire(
        self,
        *,
        heuristic_id: str,
        version: str,
        domain: str,
        context_hash: str,
        selected_by: str = "heuristic_self",
        ttl_seconds: float | None = None,
        reason_codes: list[str] | None = None,
    ) -> HeuristicDecisionLeaseDB:
        """Acquire a new lease, superseding any existing active lease for the domain."""
        effective_ttl = ttl_seconds if ttl_seconds is not None else _DOMAIN_TTL_DEFAULTS.get(domain, 7.0)
        now = time.time()

        self._supersede_active(domain, now=now)

        lease = HeuristicDecisionLeaseDB(
            id=str(uuid.uuid4()),
            heuristic_id=heuristic_id,
            version=version,
            domain=domain,
            status="active",
            selected_by=selected_by,
            context_hash=context_hash,
            ttl_seconds=effective_ttl,
            reason_codes=list(reason_codes or []),
            acquired_at=now,
            deadline_at=now + effective_ttl,
        )
        return self.save(lease)

    def release(self, lease_id: str, *, status: str = "released") -> HeuristicDecisionLeaseDB | None:
        lease = self.get_by_id(lease_id)
        if lease is None:
            return None
        if lease.status in {"released", "expired", "superseded"}:
            return lease
        lease.status = status
        lease.released_at = time.time()
        return self.save(lease)

    def get_active(self, domain: str, *, now_ts: float | None = None) -> HeuristicDecisionLeaseDB | None:
        """Return the currently active, non-expired lease for a domain."""
        now_value = float(now_ts or time.time())
        with Session(engine) as session:
            stmt = (
                select(HeuristicDecisionLeaseDB)
                .where(
                    HeuristicDecisionLeaseDB.domain == domain,
                    HeuristicDecisionLeaseDB.status == "active",
                    HeuristicDecisionLeaseDB.deadline_at > now_value,
                )
                .order_by(HeuristicDecisionLeaseDB.acquired_at.desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def list_expired(self, *, now_ts: float | None = None) -> List[HeuristicDecisionLeaseDB]:
        """Return active leases whose deadline has passed (not yet marked expired)."""
        now_value = float(now_ts or time.time())
        with Session(engine) as session:
            stmt = select(HeuristicDecisionLeaseDB).where(
                HeuristicDecisionLeaseDB.status == "active",
                HeuristicDecisionLeaseDB.deadline_at < now_value,
            )
            return list(session.exec(stmt).all())

    def mark_expired_batch(self, *, now_ts: float | None = None) -> int:
        """Mark all overdue active leases as expired. Returns count updated."""
        expired = self.list_expired(now_ts=now_ts)
        now = float(now_ts or time.time())
        for lease in expired:
            lease.status = "expired"
            lease.released_at = now
            self.save(lease)
        return len(expired)

    def list_by_domain(self, domain: str) -> List[HeuristicDecisionLeaseDB]:
        with Session(engine) as session:
            stmt = (
                select(HeuristicDecisionLeaseDB)
                .where(HeuristicDecisionLeaseDB.domain == domain)
                .order_by(HeuristicDecisionLeaseDB.acquired_at.desc())
            )
            return list(session.exec(stmt).all())

    def list_all(self) -> List[HeuristicDecisionLeaseDB]:
        with Session(engine) as session:
            stmt = select(HeuristicDecisionLeaseDB).order_by(
                HeuristicDecisionLeaseDB.acquired_at.desc()
            )
            return list(session.exec(stmt).all())

    def _supersede_active(self, domain: str, *, now: float) -> None:
        with Session(engine) as session:
            stmt = select(HeuristicDecisionLeaseDB).where(
                HeuristicDecisionLeaseDB.domain == domain,
                HeuristicDecisionLeaseDB.status == "active",
            )
            for lease in session.exec(stmt).all():
                lease.status = "superseded"
                lease.released_at = now
                session.add(lease)
            session.commit()
