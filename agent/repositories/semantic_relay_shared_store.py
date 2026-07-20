"""Database-backed shared semantic relay store for multi-Hub deployments."""

from __future__ import annotations

import threading

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.semantic_relay import SemanticRelayCursorDB, SemanticRelayEnvelopeDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.semantic_relay_repository import (
    SemanticRelayEnvelope,
    SemanticRelayRepositoryError,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from agent.services.semantic_relay_limits import DEFAULT_SEMANTIC_RELAY_LIMITS, SemanticRelayLimits

_DB_RELAY_LOCK = threading.RLock()


def _scope_key(tenant_id: str, session_id: str, audience_id: str, traffic_class: str) -> str:
    return f"{tenant_id}\x1f{session_id}\x1f{audience_id}\x1f{traffic_class}"


def _domain(row: SemanticRelayEnvelopeDB) -> SemanticRelayEnvelope:
    return SemanticRelayEnvelope(
        message_id=row.message_id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        epoch=row.epoch,
        sender_id=row.sender_id,
        audience_id=row.audience_id,
        traffic_class=row.traffic_class,
        sequence=row.sequence,
        compression=row.compression,
        security_algorithm=row.security_algorithm,
        key_id=row.key_id,
        payload_bytes=row.payload_bytes,
        payload_digest=row.payload_digest,
        ciphertext=row.ciphertext,
        expires_at=row.expires_at,
        cursor=row.cursor,
        created_at=row.created_at,
    )


class SharedSemanticRelayRepository:
    """Atomic relational implementation shared by all Hub replicas.

    PostgreSQL row locks serialize cursor allocation.  The process lock also
    gives SQLite-based tests the same deterministic contract.
    """

    def __init__(self, limits: SemanticRelayLimits = DEFAULT_SEMANTIC_RELAY_LIMITS) -> None:
        limits.validate()
        self._limits = limits

    def append(
        self,
        envelope: SemanticRelayEnvelope,
        *,
        now: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticRelayEnvelope:
        with _DB_RELAY_LOCK, Session(engine) as session:
            self._acquire_global_append_lock(session)
            self._expire_in_session(session, now=now, limit=self._limits.max_global_messages)
            existing = session.exec(
                select(SemanticRelayEnvelopeDB).where(
                    SemanticRelayEnvelopeDB.tenant_id == envelope.tenant_id,
                    SemanticRelayEnvelopeDB.session_id == envelope.session_id,
                    SemanticRelayEnvelopeDB.audience_id == envelope.audience_id,
                    SemanticRelayEnvelopeDB.message_id == envelope.message_id,
                )
            ).first()
            if existing is not None:
                if existing.payload_digest != envelope.payload_digest:
                    raise SemanticRelayRepositoryError("relay_message_id_conflict")
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                    session.commit()
                return _domain(existing)
            self._check_limits(session, envelope)
            scope_key = _scope_key(
                envelope.tenant_id,
                envelope.session_id,
                envelope.audience_id,
                envelope.traffic_class,
            )
            cursor_row = session.exec(
                select(SemanticRelayCursorDB).where(SemanticRelayCursorDB.scope_key == scope_key).with_for_update()
            ).first()
            if cursor_row is None:
                cursor_row = SemanticRelayCursorDB(
                    scope_key=scope_key,
                    tenant_id=envelope.tenant_id,
                    session_id=envelope.session_id,
                    audience_id=envelope.audience_id,
                    traffic_class=envelope.traffic_class,
                )
                session.add(cursor_row)
                session.flush()
            cursor_row.next_cursor += 1
            cursor_row.version += 1
            cursor_row.updated_at = now
            row = SemanticRelayEnvelopeDB(
                message_id=envelope.message_id,
                tenant_id=envelope.tenant_id,
                session_id=envelope.session_id,
                epoch=envelope.epoch,
                sender_id=envelope.sender_id,
                audience_id=envelope.audience_id,
                traffic_class=envelope.traffic_class,
                sequence=envelope.sequence,
                compression=envelope.compression,
                security_algorithm=envelope.security_algorithm,
                key_id=envelope.key_id,
                payload_bytes=envelope.payload_bytes,
                payload_digest=envelope.payload_digest,
                ciphertext=envelope.ciphertext,
                cursor=cursor_row.next_cursor,
                created_at=now,
                expires_at=envelope.expires_at,
            )
            session.add(cursor_row)
            session.add(row)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise SemanticRelayRepositoryError("relay_concurrent_append_conflict") from exc
            session.refresh(row)
            return _domain(row)

    def read_after(
        self,
        *,
        tenant_id: str,
        session_id: str,
        audience_id: str,
        cursor: int,
        limit: int,
        now: float,
        traffic_class: str | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> list[SemanticRelayEnvelope]:
        with Session(engine) as session:
            statement = select(SemanticRelayEnvelopeDB).where(
                SemanticRelayEnvelopeDB.tenant_id == tenant_id,
                SemanticRelayEnvelopeDB.session_id == session_id,
                SemanticRelayEnvelopeDB.audience_id == audience_id,
                SemanticRelayEnvelopeDB.cursor > max(0, int(cursor)),
                SemanticRelayEnvelopeDB.expires_at > now,
            )
            if traffic_class is not None:
                statement = statement.where(SemanticRelayEnvelopeDB.traffic_class == traffic_class)
            rows = session.exec(
                statement.order_by(SemanticRelayEnvelopeDB.cursor).limit(
                    max(1, min(int(limit), self._limits.max_batch_count))
                )
            ).all()
            return [_domain(row) for row in rows]

    def acknowledge(
        self,
        *,
        tenant_id: str,
        session_id: str,
        audience_id: str,
        cursor: int,
        now: float,
        traffic_class: str | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> int:
        with _DB_RELAY_LOCK, Session(engine) as session:
            statement = select(SemanticRelayCursorDB).where(
                SemanticRelayCursorDB.tenant_id == tenant_id,
                SemanticRelayCursorDB.session_id == session_id,
                SemanticRelayCursorDB.audience_id == audience_id,
            )
            if traffic_class is not None:
                statement = statement.where(SemanticRelayCursorDB.traffic_class == traffic_class)
            states = session.exec(statement.with_for_update()).all()
            acknowledged = 0
            changed = False
            for state in states:
                bounded = min(max(0, int(cursor)), state.next_cursor)
                previous = state.acknowledged_cursor
                state.acknowledged_cursor = max(previous, bounded)
                changed = changed or state.acknowledged_cursor > previous
                state.version += 1
                state.updated_at = now
                rows = session.exec(
                    select(SemanticRelayEnvelopeDB).where(
                        SemanticRelayEnvelopeDB.tenant_id == tenant_id,
                        SemanticRelayEnvelopeDB.session_id == session_id,
                        SemanticRelayEnvelopeDB.audience_id == audience_id,
                        SemanticRelayEnvelopeDB.traffic_class == state.traffic_class,
                        SemanticRelayEnvelopeDB.cursor <= state.acknowledged_cursor,
                    )
                ).all()
                for row in rows:
                    session.delete(row)
                session.add(state)
                acknowledged = max(acknowledged, state.acknowledged_cursor)
            if changed and audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()
            return acknowledged

    def revoke(
        self,
        *,
        tenant_id: str,
        session_id: str,
        message_id: str | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> int:
        with _DB_RELAY_LOCK, Session(engine) as session:
            statement = select(SemanticRelayEnvelopeDB).where(
                SemanticRelayEnvelopeDB.tenant_id == tenant_id,
                SemanticRelayEnvelopeDB.session_id == session_id,
            )
            if message_id is not None:
                statement = statement.where(SemanticRelayEnvelopeDB.message_id == message_id)
            rows = session.exec(statement).all()
            for row in rows:
                session.delete(row)
            if rows and audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()
            return len(rows)

    def expire(self, *, now: float, limit: int) -> int:
        with _DB_RELAY_LOCK, Session(engine) as session:
            count = self._expire_in_session(
                session,
                now=now,
                limit=max(1, min(int(limit), self._limits.max_batch_count)),
            )
            session.commit()
            return count

    @staticmethod
    def _expire_in_session(session: Session, *, now: float, limit: int) -> int:
        rows = session.exec(
            select(SemanticRelayEnvelopeDB)
            .where(SemanticRelayEnvelopeDB.expires_at <= now)
            .order_by(SemanticRelayEnvelopeDB.expires_at, SemanticRelayEnvelopeDB.cursor)
            .limit(limit)
        ).all()
        for row in rows:
            session.delete(row)
        return len(rows)

    def _check_limits(self, session: Session, envelope: SemanticRelayEnvelope) -> None:
        class_limit = self._limits.envelope_limit(envelope.traffic_class)
        if class_limit <= 0:
            raise SemanticRelayRepositoryError("relay_traffic_class_unknown")
        if envelope.payload_bytes < 0 or envelope.payload_bytes > class_limit:
            raise SemanticRelayRepositoryError("relay_envelope_too_large")

        def count_and_bytes(*conditions) -> tuple[int, int]:
            count = session.exec(select(func.count()).select_from(SemanticRelayEnvelopeDB).where(*conditions)).one()
            size = session.exec(
                select(func.coalesce(func.sum(SemanticRelayEnvelopeDB.payload_bytes), 0)).where(*conditions)
            ).one()
            return int(count), int(size)

        global_count, global_bytes = count_and_bytes()
        session_count, session_bytes = count_and_bytes(
            SemanticRelayEnvelopeDB.tenant_id == envelope.tenant_id,
            SemanticRelayEnvelopeDB.session_id == envelope.session_id,
        )
        peer_count, peer_bytes = count_and_bytes(
            SemanticRelayEnvelopeDB.tenant_id == envelope.tenant_id,
            SemanticRelayEnvelopeDB.session_id == envelope.session_id,
            SemanticRelayEnvelopeDB.audience_id == envelope.audience_id,
        )
        distinct_sessions = len(
            session.exec(
                select(SemanticRelayEnvelopeDB.tenant_id, SemanticRelayEnvelopeDB.session_id).group_by(
                    SemanticRelayEnvelopeDB.tenant_id,
                    SemanticRelayEnvelopeDB.session_id,
                )
            ).all()
        )
        distinct_peers = len(
            session.exec(
                select(SemanticRelayEnvelopeDB.audience_id)
                .where(
                    SemanticRelayEnvelopeDB.tenant_id == envelope.tenant_id,
                    SemanticRelayEnvelopeDB.session_id == envelope.session_id,
                )
                .group_by(SemanticRelayEnvelopeDB.audience_id)
            ).all()
        )
        checks = (
            (
                global_count + 1,
                self._limits.global_message_limit(envelope.traffic_class),
                "relay_global_message_quota",
            ),
            (
                global_bytes + envelope.payload_bytes,
                self._limits.global_byte_limit(envelope.traffic_class),
                "relay_global_byte_quota",
            ),
            (session_count + 1, self._limits.max_session_messages, "relay_session_message_quota"),
            (session_bytes + envelope.payload_bytes, self._limits.max_session_bytes, "relay_session_byte_quota"),
            (peer_count + 1, self._limits.max_peer_messages, "relay_peer_message_quota"),
            (peer_bytes + envelope.payload_bytes, self._limits.max_peer_bytes, "relay_peer_byte_quota"),
            (
                distinct_sessions + (0 if session_count else 1),
                self._limits.max_sessions,
                "relay_session_quota",
            ),
            (
                distinct_peers + (0 if peer_count else 1),
                self._limits.max_peers_per_session,
                "relay_peer_quota",
            ),
        )
        for actual, maximum, reason in checks:
            if actual > maximum:
                raise SemanticRelayRepositoryError(reason)

    @staticmethod
    def _acquire_global_append_lock(session: Session) -> None:
        """Serialize quota checks across PostgreSQL Hub processes.

        SQLite is retained for local/single-process tests; supported multi-Hub
        deployments use the shared PostgreSQL database.
        """

        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": 7_184_221_903})


__all__ = ["SharedSemanticRelayRepository"]
