"""Ports of the content-free semantic-media audit trail.

``SemanticMediaAuditPort`` is the narrow write port that domain services and
authority repositories receive by injection; ``SemanticMediaAuditRepository``
is the persistence port of the audit store. Both are pure Protocols over the
model contracts, so repositories can type against them without importing the
audit service (DIP/ISP). ``agent.services.semantic_media_audit_service``
re-exports both names unchanged.
"""

from __future__ import annotations

from typing import Protocol

from agent.models.semantic_media_audit import SemanticMediaAuditEvent


class SemanticMediaAuditRepository(Protocol):
    def append_once(self, event: SemanticMediaAuditEvent) -> tuple[SemanticMediaAuditEvent, bool]: ...

    def page(
        self,
        *,
        tenant_digest: str,
        scope_digest: str,
        after_event_id: str | None,
        limit: int,
        now_ms: int,
    ) -> tuple[tuple[SemanticMediaAuditEvent, ...], str | None]: ...

    def delete_expired(self, *, now_ms: int, limit: int) -> int: ...

    def delete_scope(self, *, tenant_digest: str, scope_digest: str, limit: int) -> int: ...

    def delete_tenant(self, *, tenant_digest: str, limit: int) -> int: ...


class SemanticMediaAuditPort(Protocol):
    """Narrow write-only port used by Hub domain services (DIP/ISP)."""

    def record_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> tuple[SemanticMediaAuditEvent, bool]: ...

    def prepare_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> SemanticMediaAuditEvent: ...

    def append_prepared(
        self,
        event: SemanticMediaAuditEvent,
    ) -> tuple[SemanticMediaAuditEvent, bool]: ...


__all__ = ["SemanticMediaAuditPort", "SemanticMediaAuditRepository"]
