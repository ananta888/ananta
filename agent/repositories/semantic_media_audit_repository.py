"""SQL persistence adapter for content-free semantic-media audit events."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SemanticMediaAuditEventDB, SemanticMediaAuditOutboxDB
from agent.services.semantic_media_audit_service import (
    MAX_SCOPE_EVENTS,
    SemanticMediaAuditError,
    SemanticMediaAuditEvent,
    same_idempotent_audit_request,
)


class SqlSemanticMediaAuditRepository:
    """Durable, bounded adapter implementing ``SemanticMediaAuditRepository``.

    The idempotency unique constraint is the cross-process exactly-once fence.
    Page queries are always tenant- and scope-bound and never return raw
    principals because the table contains digests only.
    """

    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def append_once(
        self,
        event: SemanticMediaAuditEvent,
    ) -> tuple[SemanticMediaAuditEvent, bool]:
        with Session(self._engine) as db:
            existing = self._by_idempotency(db, event.idempotency_digest)
            if existing is not None:
                return self._replay(existing, event)
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
            db.add(self._to_row(event))
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                existing = self._by_idempotency(db, event.idempotency_digest)
                if existing is None:
                    raise SemanticMediaAuditError(
                        "audit_persistence_conflict",
                        status_code=409,
                    ) from exc
                return self._replay(existing, event)
        return event, True

    def page(
        self,
        *,
        tenant_digest: str,
        scope_digest: str,
        after_event_id: str | None,
        limit: int,
        now_ms: int,
    ) -> tuple[tuple[SemanticMediaAuditEvent, ...], str | None]:
        with Session(self._engine) as db:
            statement = select(SemanticMediaAuditEventDB).where(
                SemanticMediaAuditEventDB.tenant_digest == tenant_digest,
                SemanticMediaAuditEventDB.scope_digest == scope_digest,
                SemanticMediaAuditEventDB.expires_at_ms > now_ms,
            )
            if after_event_id is not None:
                cursor = db.exec(
                    select(SemanticMediaAuditEventDB).where(
                        SemanticMediaAuditEventDB.id == after_event_id,
                        SemanticMediaAuditEventDB.tenant_digest == tenant_digest,
                        SemanticMediaAuditEventDB.scope_digest == scope_digest,
                        SemanticMediaAuditEventDB.expires_at_ms > now_ms,
                    )
                ).first()
                if cursor is None:
                    raise SemanticMediaAuditError("audit_cursor_invalid", status_code=400)
                statement = statement.where(
                    sa.or_(
                        SemanticMediaAuditEventDB.created_at_ms > cursor.created_at_ms,
                        sa.and_(
                            SemanticMediaAuditEventDB.created_at_ms == cursor.created_at_ms,
                            SemanticMediaAuditEventDB.id > cursor.id,
                        ),
                    )
                )
            rows = list(
                db.exec(
                    statement.order_by(
                        SemanticMediaAuditEventDB.created_at_ms,
                        SemanticMediaAuditEventDB.id,
                    ).limit(limit + 1)
                )
            )
        page_rows = rows[:limit]
        next_cursor = page_rows[-1].id if len(rows) > limit and page_rows else None
        return tuple(self._from_row(row) for row in page_rows), next_cursor

    def delete_expired(self, *, now_ms: int, limit: int) -> int:
        with Session(self._engine) as db:
            ids = list(
                db.exec(
                    select(SemanticMediaAuditEventDB.id)
                    .where(SemanticMediaAuditEventDB.expires_at_ms <= now_ms)
                    .order_by(
                        SemanticMediaAuditEventDB.expires_at_ms,
                        SemanticMediaAuditEventDB.id,
                    )
                    .limit(limit)
                )
            )
            if not ids:
                return 0
            result = db.exec(
                sa.delete(SemanticMediaAuditEventDB).where(
                    SemanticMediaAuditEventDB.id.in_(ids)
                )
            )
            db.commit()
            return int(result.rowcount or 0)

    def delete_scope(self, *, tenant_digest: str, scope_digest: str, limit: int) -> int:
        deleted = self._delete_matching(
            SemanticMediaAuditEventDB.tenant_digest == tenant_digest,
            SemanticMediaAuditEventDB.scope_digest == scope_digest,
            limit=limit,
        )
        if deleted < limit:
            deleted += self._delete_pending(
                SemanticMediaAuditOutboxDB.tenant_digest == tenant_digest,
                SemanticMediaAuditOutboxDB.scope_digest == scope_digest,
                limit=limit - deleted,
            )
        return deleted

    def delete_tenant(self, *, tenant_digest: str, limit: int) -> int:
        deleted = self._delete_matching(
            SemanticMediaAuditEventDB.tenant_digest == tenant_digest,
            limit=limit,
        )
        if deleted < limit:
            deleted += self._delete_pending(
                SemanticMediaAuditOutboxDB.tenant_digest == tenant_digest,
                limit=limit - deleted,
            )
        return deleted

    def _delete_matching(self, *predicates, limit: int) -> int:
        with Session(self._engine) as db:
            ids = list(
                db.exec(
                    select(SemanticMediaAuditEventDB.id)
                    .where(*predicates)
                    .order_by(SemanticMediaAuditEventDB.created_at_ms, SemanticMediaAuditEventDB.id)
                    .limit(limit)
                )
            )
            if not ids:
                return 0
            result = db.exec(
                sa.delete(SemanticMediaAuditEventDB).where(SemanticMediaAuditEventDB.id.in_(ids))
            )
            db.commit()
            return int(result.rowcount or 0)

    def _delete_pending(self, *predicates, limit: int) -> int:
        with Session(self._engine) as db:
            ids = list(
                db.exec(
                    select(SemanticMediaAuditOutboxDB.id)
                    .where(*predicates)
                    .order_by(
                        SemanticMediaAuditOutboxDB.created_at_ms,
                        SemanticMediaAuditOutboxDB.id,
                    )
                    .limit(limit)
                )
            )
            if not ids:
                return 0
            result = db.exec(
                sa.delete(SemanticMediaAuditOutboxDB).where(
                    SemanticMediaAuditOutboxDB.id.in_(ids)
                )
            )
            db.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _by_idempotency(
        db: Session,
        digest: str,
    ) -> SemanticMediaAuditEventDB | None:
        return db.exec(
            select(SemanticMediaAuditEventDB).where(
                SemanticMediaAuditEventDB.idempotency_digest == digest
            )
        ).first()

    @classmethod
    def _replay(
        cls,
        row: SemanticMediaAuditEventDB,
        event: SemanticMediaAuditEvent,
    ) -> tuple[SemanticMediaAuditEvent, bool]:
        persisted = cls._from_row(row)
        if not same_idempotent_audit_request(persisted, event):
            raise SemanticMediaAuditError(
                "audit_idempotency_conflict",
                status_code=409,
            )
        return persisted, False

    @staticmethod
    def _to_row(event: SemanticMediaAuditEvent) -> SemanticMediaAuditEventDB:
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
    def _from_row(row: SemanticMediaAuditEventDB) -> SemanticMediaAuditEvent:
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


__all__ = ["SqlSemanticMediaAuditRepository"]
