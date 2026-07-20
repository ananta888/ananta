"""Repository port and deterministic bounded in-memory semantic relay."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Protocol

from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditEvent,
    same_idempotent_audit_request,
)
from agent.services.semantic_relay_limits import DEFAULT_SEMANTIC_RELAY_LIMITS, SemanticRelayLimits


class SemanticRelayRepositoryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class SemanticRelayEnvelope:
    message_id: str
    tenant_id: str
    session_id: str
    epoch: int
    sender_id: str
    audience_id: str
    traffic_class: str
    payload_bytes: int
    payload_digest: str
    ciphertext: str
    expires_at: float
    sequence: int = 1
    compression: str = "none"
    security_algorithm: str = "AES-GCM-256"
    key_id: str = "legacy-relay-key"
    cursor: int = 0
    created_at: float = 0.0


class SemanticRelayRepository(Protocol):
    def append(
        self,
        envelope: SemanticRelayEnvelope,
        *,
        now: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticRelayEnvelope: ...

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
    ) -> list[SemanticRelayEnvelope]: ...

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
    ) -> int: ...

    def revoke(
        self,
        *,
        tenant_id: str,
        session_id: str,
        message_id: str | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> int: ...

    def expire(self, *, now: float, limit: int) -> int: ...


class InMemorySemanticRelayRepository:
    """Substitutable test repository with the same quotas as shared storage."""

    def __init__(self, limits: SemanticRelayLimits = DEFAULT_SEMANTIC_RELAY_LIMITS) -> None:
        limits.validate()
        self._limits = limits
        self._lock = threading.RLock()
        self._rows: dict[tuple[str, str, str, str], SemanticRelayEnvelope] = {}
        self._next_cursor: dict[tuple[str, str, str, str], int] = {}
        self._ack_cursor: dict[tuple[str, str, str, str], int] = {}
        self._audit_events: dict[str, SemanticMediaAuditEvent] = {}

    def append(
        self,
        envelope: SemanticRelayEnvelope,
        *,
        now: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticRelayEnvelope:
        with self._lock:
            self._validate_audit(audit_event)
            self._expire_locked(now=now, limit=self._limits.max_global_messages)
            key = (envelope.tenant_id, envelope.session_id, envelope.audience_id, envelope.message_id)
            existing = self._rows.get(key)
            if existing is not None:
                if existing.payload_digest != envelope.payload_digest:
                    raise SemanticRelayRepositoryError("relay_message_id_conflict")
                self._stage_audit(audit_event)
                return existing
            self._check_limits(envelope)
            scope = (envelope.tenant_id, envelope.session_id, envelope.audience_id, envelope.traffic_class)
            cursor = self._next_cursor.get(scope, 0) + 1
            self._next_cursor[scope] = cursor
            stored = replace(envelope, cursor=cursor, created_at=now)
            self._rows[key] = stored
            self._stage_audit(audit_event)
            return stored

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
        with self._lock:
            self._expire_locked(now=now, limit=self._limits.max_global_messages)
            bounded_limit = max(1, min(int(limit), self._limits.max_batch_count))
            return sorted(
                (
                    row
                    for row in self._rows.values()
                    if row.tenant_id == tenant_id
                    and row.session_id == session_id
                    and row.audience_id == audience_id
                    and (traffic_class is None or row.traffic_class == traffic_class)
                    and row.cursor > max(0, int(cursor))
                ),
                key=lambda row: row.cursor,
            )[:bounded_limit]

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
        with self._lock:
            self._validate_audit(audit_event)
            self._expire_locked(now=now, limit=self._limits.max_global_messages)
            scopes = [
                scope
                for scope in self._next_cursor
                if scope[:3] == (tenant_id, session_id, audience_id)
                and (traffic_class is None or scope[3] == traffic_class)
            ]
            acknowledged = 0
            changed = False
            for scope in scopes:
                latest = self._next_cursor.get(scope, 0)
                bounded = min(max(0, int(cursor)), latest)
                previous = self._ack_cursor.get(scope, 0)
                self._ack_cursor[scope] = max(previous, bounded)
                scoped_ack = self._ack_cursor[scope]
                changed = changed or scoped_ack > previous
                acknowledged = max(acknowledged, scoped_ack)
                for key, row in list(self._rows.items()):
                    if key[:3] == scope[:3] and row.traffic_class == scope[3] and row.cursor <= scoped_ack:
                        self._rows.pop(key, None)
            if changed:
                self._stage_audit(audit_event)
            return acknowledged

    def revoke(
        self,
        *,
        tenant_id: str,
        session_id: str,
        message_id: str | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> int:
        with self._lock:
            self._validate_audit(audit_event)
            removed = 0
            for key, row in list(self._rows.items()):
                if row.tenant_id != tenant_id or row.session_id != session_id:
                    continue
                if message_id is not None and row.message_id != message_id:
                    continue
                self._rows.pop(key, None)
                removed += 1
            if removed:
                self._stage_audit(audit_event)
            return removed

    def _stage_audit(self, event: SemanticMediaAuditEvent | None) -> None:
        if event is None:
            return
        self._validate_audit(event)
        self._audit_events[event.idempotency_digest] = event

    def _validate_audit(self, event: SemanticMediaAuditEvent | None) -> None:
        if event is None:
            return
        existing = self._audit_events.get(event.idempotency_digest)
        if existing is not None and not same_idempotent_audit_request(existing, event):
            raise SemanticMediaAuditError("audit_idempotency_conflict", status_code=409)

    def expire(self, *, now: float, limit: int) -> int:
        with self._lock:
            return self._expire_locked(now=now, limit=max(1, min(int(limit), self._limits.max_batch_count)))

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "messages": len(self._rows),
                "bytes": sum(row.payload_bytes for row in self._rows.values()),
                "cursors": len(self._next_cursor),
                "ack_cursors": len(self._ack_cursor),
                "audit_events": len(self._audit_events),
            }

    def audit_events(self) -> tuple[SemanticMediaAuditEvent, ...]:
        with self._lock:
            return tuple(self._audit_events.values())

    def _expire_locked(self, *, now: float, limit: int) -> int:
        expired = sorted(
            (row for row in self._rows.values() if row.expires_at <= now),
            key=lambda row: (row.expires_at, row.cursor, row.message_id),
        )[:limit]
        for row in expired:
            self._rows.pop((row.tenant_id, row.session_id, row.audience_id, row.message_id), None)
        return len(expired)

    def _check_limits(self, envelope: SemanticRelayEnvelope) -> None:
        class_limit = self._limits.envelope_limit(envelope.traffic_class)
        if class_limit <= 0:
            raise SemanticRelayRepositoryError("relay_traffic_class_unknown")
        if envelope.payload_bytes < 0 or envelope.payload_bytes > class_limit:
            raise SemanticRelayRepositoryError("relay_envelope_too_large")
        rows = list(self._rows.values())
        session_rows = [
            row for row in rows if row.tenant_id == envelope.tenant_id and row.session_id == envelope.session_id
        ]
        peer_rows = [row for row in session_rows if row.audience_id == envelope.audience_id]
        scopes = {(row.tenant_id, row.session_id) for row in rows}
        peers = {row.audience_id for row in session_rows}
        checks = (
            (
                len(rows) + 1,
                self._limits.global_message_limit(envelope.traffic_class),
                "relay_global_message_quota",
            ),
            (
                sum(row.payload_bytes for row in rows) + envelope.payload_bytes,
                self._limits.global_byte_limit(envelope.traffic_class),
                "relay_global_byte_quota",
            ),
            (len(session_rows) + 1, self._limits.max_session_messages, "relay_session_message_quota"),
            (
                sum(row.payload_bytes for row in session_rows) + envelope.payload_bytes,
                self._limits.max_session_bytes,
                "relay_session_byte_quota",
            ),
            (len(peer_rows) + 1, self._limits.max_peer_messages, "relay_peer_message_quota"),
            (
                sum(row.payload_bytes for row in peer_rows) + envelope.payload_bytes,
                self._limits.max_peer_bytes,
                "relay_peer_byte_quota",
            ),
            (
                len(scopes | {(envelope.tenant_id, envelope.session_id)}),
                self._limits.max_sessions,
                "relay_session_quota",
            ),
            (
                len(peers | {envelope.audience_id}),
                self._limits.max_peers_per_session,
                "relay_peer_quota",
            ),
        )
        for actual, maximum, reason in checks:
            if actual > maximum:
                raise SemanticRelayRepositoryError(reason)


__all__ = [
    "InMemorySemanticRelayRepository",
    "SemanticRelayEnvelope",
    "SemanticRelayRepository",
    "SemanticRelayRepositoryError",
]
