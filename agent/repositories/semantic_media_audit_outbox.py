"""Transactional outbox for authoritative semantic-media audit transitions."""

from __future__ import annotations

import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SemanticMediaAuditEventDB, SemanticMediaAuditOutboxDB
from agent.services.semantic_media_audit_service import (
    MAX_SCOPE_EVENTS,
    SemanticMediaAuditError,
    SemanticMediaAuditEvent,
    same_idempotent_audit_request,
)
from agent.services.semantic_media_program_evidence import assert_content_free


@dataclass(frozen=True, slots=True)
class SemanticMediaAuditDispatchResult:
    attempted: int
    delivered: int
    replayed: int
    failed: int
    pending: int


class SqlSemanticMediaAuditOutbox:
    """Hub-owned enqueue and delivery adapter.

    ``enqueue_in_session`` never commits: the caller owns the transaction and
    therefore commits the domain mutation and audit command atomically. The
    dispatcher materializes the final audit row and removes the pending row in
    one second transaction. Unique event/idempotency constraints are the
    multi-Hub exactly-once fence.
    """

    def __init__(self, *, db_engine=default_engine, clock_ms=lambda: time.time_ns() // 1_000_000) -> None:
        self._engine = db_engine
        self._clock_ms = clock_ms

    @classmethod
    def enqueue_in_session(
        cls,
        db: Session,
        event: SemanticMediaAuditEvent,
    ) -> bool:
        """Stage ``event`` without committing the caller's transaction."""

        assert_content_free(event.public())
        existing_event = db.exec(
            select(SemanticMediaAuditEventDB).where(
                SemanticMediaAuditEventDB.idempotency_digest == event.idempotency_digest
            )
        ).first()
        if existing_event is not None:
            cls._assert_same(cls._event_from_row(existing_event), event)
            return False
        existing_outbox = db.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.idempotency_digest == event.idempotency_digest
            )
        ).first()
        if existing_outbox is not None:
            cls._assert_same(cls._event_from_outbox(existing_outbox), event)
            return False

        final_count = db.exec(
            select(sa.func.count(SemanticMediaAuditEventDB.id)).where(
                SemanticMediaAuditEventDB.tenant_digest == event.tenant_digest,
                SemanticMediaAuditEventDB.scope_digest == event.scope_digest,
                SemanticMediaAuditEventDB.expires_at_ms > event.created_at_ms,
            )
        ).one()
        pending_count = db.exec(
            select(sa.func.count(SemanticMediaAuditOutboxDB.id)).where(
                SemanticMediaAuditOutboxDB.tenant_digest == event.tenant_digest,
                SemanticMediaAuditOutboxDB.scope_digest == event.scope_digest,
                SemanticMediaAuditOutboxDB.expires_at_ms > event.created_at_ms,
            )
        ).one()
        if int(final_count) + int(pending_count) >= MAX_SCOPE_EVENTS:
            raise SemanticMediaAuditError(
                "audit_scope_cardinality_exceeded",
                status_code=429,
            )
        db.add(cls._outbox_row(event))
        return True

    def dispatch_pending(self, *, limit: int = 100) -> SemanticMediaAuditDispatchResult:
        if not 1 <= limit <= 1_000:
            raise SemanticMediaAuditError("audit_dispatch_limit_invalid")
        now_ms = int(self._clock_ms())
        with Session(self._engine) as db:
            ids = list(
                db.exec(
                    select(SemanticMediaAuditOutboxDB.id)
                    .where(SemanticMediaAuditOutboxDB.available_at_ms <= now_ms)
                    .order_by(
                        SemanticMediaAuditOutboxDB.available_at_ms,
                        SemanticMediaAuditOutboxDB.created_at_ms,
                        SemanticMediaAuditOutboxDB.id,
                    )
                    .limit(limit)
                )
            )
        delivered = 0
        replayed = 0
        failed = 0
        for outbox_id in ids:
            try:
                outcome = self._dispatch_one(outbox_id)
            except (SQLAlchemyError, SemanticMediaAuditError):
                failed += 1
                continue
            if outcome == "delivered":
                delivered += 1
            elif outcome == "replayed":
                replayed += 1
        return SemanticMediaAuditDispatchResult(
            attempted=len(ids),
            delivered=delivered,
            replayed=replayed,
            failed=failed,
            pending=self.pending_count(),
        )

    def pending_count(self) -> int:
        with Session(self._engine) as db:
            return int(db.exec(select(sa.func.count(SemanticMediaAuditOutboxDB.id))).one())

    def _dispatch_one(self, outbox_id: str) -> str:
        with Session(self._engine) as db:
            row = db.exec(
                select(SemanticMediaAuditOutboxDB)
                .where(SemanticMediaAuditOutboxDB.id == outbox_id)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                return "absent"
            event = self._event_from_outbox(row)
            existing = db.exec(
                select(SemanticMediaAuditEventDB).where(
                    SemanticMediaAuditEventDB.idempotency_digest == event.idempotency_digest
                )
            ).first()
            if existing is not None:
                self._assert_same(self._event_from_row(existing), event)
                db.delete(row)
                db.commit()
                return "replayed"
            scope_count = db.exec(
                select(sa.func.count(SemanticMediaAuditEventDB.id)).where(
                    SemanticMediaAuditEventDB.tenant_digest == event.tenant_digest,
                    SemanticMediaAuditEventDB.scope_digest == event.scope_digest,
                    SemanticMediaAuditEventDB.expires_at_ms > event.created_at_ms,
                )
            ).one()
            if int(scope_count) >= MAX_SCOPE_EVENTS:
                raise SemanticMediaAuditError(
                    "audit_scope_cardinality_exceeded",
                    status_code=429,
                )
            db.add(self._event_row(event))
            db.delete(row)
            db.commit()
            return "delivered"

    @staticmethod
    def _assert_same(first: SemanticMediaAuditEvent, second: SemanticMediaAuditEvent) -> None:
        if not same_idempotent_audit_request(first, second):
            raise SemanticMediaAuditError("audit_idempotency_conflict", status_code=409)

    @staticmethod
    def _outbox_row(event: SemanticMediaAuditEvent) -> SemanticMediaAuditOutboxDB:
        return SemanticMediaAuditOutboxDB(
            id=f"audit-outbox-{event.idempotency_digest[:40]}",
            event_id=event.event_id,
            idempotency_digest=event.idempotency_digest,
            tenant_digest=event.tenant_digest,
            scope_digest=event.scope_digest,
            event_type=event.event_type,
            transition=event.transition,
            reason_code=event.reason_code,
            epoch=event.epoch,
            contract_ref=event.contract_ref,
            lease_ref=event.lease_ref,
            job_ref=event.job_ref,
            created_at_ms=event.created_at_ms,
            expires_at_ms=event.expires_at_ms,
            available_at_ms=event.created_at_ms,
        )

    @staticmethod
    def _event_row(event: SemanticMediaAuditEvent) -> SemanticMediaAuditEventDB:
        return SemanticMediaAuditEventDB(
            id=event.event_id,
            idempotency_digest=event.idempotency_digest,
            tenant_digest=event.tenant_digest,
            scope_digest=event.scope_digest,
            event_type=event.event_type,
            transition=event.transition,
            reason_code=event.reason_code,
            epoch=event.epoch,
            contract_ref=event.contract_ref,
            lease_ref=event.lease_ref,
            job_ref=event.job_ref,
            created_at_ms=event.created_at_ms,
            expires_at_ms=event.expires_at_ms,
        )

    @staticmethod
    def _event_from_row(row: SemanticMediaAuditEventDB) -> SemanticMediaAuditEvent:
        return SemanticMediaAuditEvent(
            event_id=row.id,
            idempotency_digest=row.idempotency_digest,
            tenant_digest=row.tenant_digest,
            scope_digest=row.scope_digest,
            event_type=row.event_type,
            transition=row.transition,
            reason_code=row.reason_code,
            epoch=row.epoch,
            contract_ref=row.contract_ref,
            lease_ref=row.lease_ref,
            job_ref=row.job_ref,
            created_at_ms=row.created_at_ms,
            expires_at_ms=row.expires_at_ms,
        )

    @staticmethod
    def _event_from_outbox(row: SemanticMediaAuditOutboxDB) -> SemanticMediaAuditEvent:
        return SemanticMediaAuditEvent(
            event_id=row.event_id,
            idempotency_digest=row.idempotency_digest,
            tenant_digest=row.tenant_digest,
            scope_digest=row.scope_digest,
            event_type=row.event_type,
            transition=row.transition,
            reason_code=row.reason_code,
            epoch=row.epoch,
            contract_ref=row.contract_ref,
            lease_ref=row.lease_ref,
            job_ref=row.job_ref,
            created_at_ms=row.created_at_ms,
            expires_at_ms=row.expires_at_ms,
        )


__all__ = [
    "SemanticMediaAuditDispatchResult",
    "SqlSemanticMediaAuditOutbox",
]
