"""Role-scoped export and erasure lifecycle for pseudonymous audit rows."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from agent.services.semantic_media_audit_service import (
    MAX_PAGE_SIZE,
    MAX_SCOPE_EVENTS,
    SemanticMediaAuditError,
    SemanticMediaAuditRepository,
)
from agent.services.semantic_media_program_evidence import assert_content_free, canonical_sha256

EXPORT_ROLES = frozenset({"semantic_media_auditor", "semantic_media_privacy_officer"})
ERASURE_ROLES = frozenset({"semantic_media_privacy_officer"})
MAX_EXPORT_EVENTS = 1_000


@dataclass(frozen=True, slots=True)
class SemanticMediaAuditLifecyclePrincipal:
    tenant_digest: str
    subject_digest: str
    roles: frozenset[str]


class SemanticMediaAuditLifecycleService:
    """Purpose-specific privacy port; never grants task or feature authority."""

    def __init__(
        self,
        repository: SemanticMediaAuditRepository,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms

    def export_scope(
        self,
        principal: SemanticMediaAuditLifecyclePrincipal,
        *,
        scope_digest: str,
    ) -> dict[str, object]:
        self._authorize(principal, EXPORT_ROLES, "semantic_audit_export_forbidden")
        self._digest(scope_digest)
        now_ms = int(self._clock_ms())
        events: list[dict[str, object]] = []
        cursor: str | None = None
        while True:
            rows, cursor = self._repository.page(
                tenant_digest=principal.tenant_digest,
                scope_digest=scope_digest,
                after_event_id=cursor,
                limit=MAX_PAGE_SIZE,
                now_ms=now_ms,
            )
            events.extend(row.public() for row in rows)
            if len(events) > MAX_EXPORT_EVENTS:
                raise SemanticMediaAuditError("semantic_audit_export_limit_exceeded", status_code=413)
            if cursor is None:
                break
        body: dict[str, object] = {
            "schema": "ananta.semantic-media-audit-export.v1",
            "scope_digest": scope_digest,
            "generated_at_ms": now_ms,
            "event_count": len(events),
            "events": events,
        }
        body["export_digest"] = canonical_sha256(body)
        assert_content_free(body)
        return body

    def erase_scope(
        self,
        principal: SemanticMediaAuditLifecyclePrincipal,
        *,
        scope_digest: str,
    ) -> int:
        self._authorize(principal, ERASURE_ROLES, "semantic_audit_erasure_forbidden")
        self._digest(scope_digest)
        return self._erase(
            lambda: self._repository.delete_scope(
                tenant_digest=principal.tenant_digest,
                scope_digest=scope_digest,
                limit=MAX_PAGE_SIZE,
            )
        )

    def erase_tenant(self, principal: SemanticMediaAuditLifecyclePrincipal) -> int:
        self._authorize(principal, ERASURE_ROLES, "semantic_audit_erasure_forbidden")
        return self._erase(
            lambda: self._repository.delete_tenant(
                tenant_digest=principal.tenant_digest,
                limit=MAX_PAGE_SIZE,
            )
        )

    @staticmethod
    def _authorize(
        principal: SemanticMediaAuditLifecyclePrincipal,
        allowed: frozenset[str],
        reason_code: str,
    ) -> None:
        SemanticMediaAuditLifecycleService._digest(principal.tenant_digest)
        SemanticMediaAuditLifecycleService._digest(principal.subject_digest)
        if not principal.roles & allowed:
            raise SemanticMediaAuditError(reason_code, status_code=403)

    @staticmethod
    def _digest(value: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise SemanticMediaAuditError("semantic_audit_scope_invalid", status_code=400)

    @staticmethod
    def _erase(delete_batch: Callable[[], int]) -> int:
        deleted = 0
        while deleted <= MAX_SCOPE_EVENTS:
            count = delete_batch()
            deleted += count
            if count < MAX_PAGE_SIZE:
                return deleted
        raise SemanticMediaAuditError("semantic_audit_erasure_limit_exceeded", status_code=409)


__all__ = [
    "ERASURE_ROLES",
    "EXPORT_ROLES",
    "SemanticMediaAuditLifecyclePrincipal",
    "SemanticMediaAuditLifecycleService",
]
