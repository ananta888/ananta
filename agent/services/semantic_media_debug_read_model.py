"""Read-only, role-scoped projection over content-free semantic-media audit."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from agent.services.semantic_media_audit_service import (
    MAX_PAGE_SIZE,
    SemanticMediaAuditError,
    SemanticMediaAuditRepository,
)

DEBUG_ROLES = frozenset({"semantic_media_auditor", "semantic_media_operator"})


@dataclass(frozen=True, slots=True)
class SemanticMediaDebugPrincipal:
    tenant_digest: str
    subject_digest: str
    roles: frozenset[str]


class SemanticMediaDebugReadModel:
    """No mutation methods are exposed by design (CQRS/read-model boundary)."""

    def __init__(
        self,
        repository: SemanticMediaAuditRepository,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms

    def page(
        self,
        principal: SemanticMediaDebugPrincipal,
        *,
        scope_digest: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        if not principal.roles & DEBUG_ROLES:
            raise SemanticMediaAuditError("semantic_debug_forbidden", status_code=403)
        if len(scope_digest) != 64 or any(character not in "0123456789abcdef" for character in scope_digest):
            raise SemanticMediaAuditError("semantic_debug_scope_invalid", status_code=400)
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise SemanticMediaAuditError("semantic_debug_limit_invalid", status_code=400)
        rows, next_cursor = self._repository.page(
            tenant_digest=principal.tenant_digest,
            scope_digest=scope_digest,
            after_event_id=cursor,
            limit=limit,
            now_ms=int(self._clock_ms()),
        )
        return {
            "items": [row.public() for row in rows],
            "next_cursor": next_cursor,
            "read_only": True,
        }


__all__ = ["DEBUG_ROLES", "SemanticMediaDebugPrincipal", "SemanticMediaDebugReadModel"]
