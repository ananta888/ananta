"""DecisionTraceRepository — persists DecisionTrace records to DB."""
from __future__ import annotations

import time
from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import DecisionTraceDB
from agent.services.heuristic_runtime.decision_trace import DecisionTrace


class DecisionTraceRepository:
    def save(self, trace: DecisionTrace) -> DecisionTraceDB:
        row = DecisionTraceDB(
            id=trace.event_id,
            surface=trace.surface,
            context_hash=trace.context_hash,
            lease_id=trace.lease_id,
            heuristic_id=trace.heuristic_id,
            strategy_id=trace.strategy_id,
            rule_id=trace.rule_id,
            confidence=trace.confidence,
            fallback_reason=trace.fallback_reason,
            source=trace.source,
            action_kind=trace.action_kind,
            started_at=trace.started_at,
            resolved_at=trace.resolved_at,
            reason_codes=list(trace.reason_codes),
        )
        with Session(engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_by_id(self, event_id: str) -> DecisionTraceDB | None:
        with Session(engine) as session:
            return session.get(DecisionTraceDB, event_id)

    def list_by_surface(
        self,
        surface: str,
        *,
        limit: int = 100,
        since_ts: float | None = None,
    ) -> List[DecisionTraceDB]:
        with Session(engine) as session:
            stmt = (
                select(DecisionTraceDB)
                .where(DecisionTraceDB.surface == surface)
            )
            if since_ts is not None:
                stmt = stmt.where(DecisionTraceDB.started_at >= since_ts)
            stmt = stmt.order_by(DecisionTraceDB.started_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    def list_fallbacks(
        self,
        *,
        surface: str | None = None,
        since_ts: float | None = None,
        limit: int = 50,
    ) -> List[DecisionTraceDB]:
        with Session(engine) as session:
            stmt = select(DecisionTraceDB).where(
                DecisionTraceDB.fallback_reason.isnot(None)
            )
            if surface:
                stmt = stmt.where(DecisionTraceDB.surface == surface)
            if since_ts is not None:
                stmt = stmt.where(DecisionTraceDB.started_at >= since_ts)
            stmt = stmt.order_by(DecisionTraceDB.started_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    def count_by_source(self, surface: str, *, since_ts: float | None = None) -> dict[str, int]:
        rows = self.list_by_surface(surface, limit=10000, since_ts=since_ts)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.source] = counts.get(r.source, 0) + 1
        return counts
