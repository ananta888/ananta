"""Capability and object-scope authorization for Kanban projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ananta_contracts.kanban import KanbanCapability


ALL_CAPABILITIES = frozenset(KanbanCapability)
OPERATOR_CAPABILITIES = frozenset(
    {
        KanbanCapability.READ,
        KanbanCapability.WRITE,
        KanbanCapability.ASSIGN,
        KanbanCapability.COMMENT,
    }
)
USER_CAPABILITIES = frozenset({KanbanCapability.READ, KanbanCapability.COMMENT})


@dataclass(frozen=True)
class KanbanPrincipal:
    subject: str
    role: str = "user"
    tenant_id: str | None = None
    team_id: str | None = None
    declared_capabilities: frozenset[KanbanCapability] = frozenset()
    is_admin: bool = False


class KanbanAuthorizationError(PermissionError):
    pass


class KanbanAuthorizationService:
    def capabilities_for(self, principal: KanbanPrincipal) -> frozenset[KanbanCapability]:
        if principal.is_admin or principal.role.lower() == "admin":
            return ALL_CAPABILITIES
        defaults = OPERATOR_CAPABILITIES if principal.role.lower() == "operator" else USER_CAPABILITIES
        return defaults if not principal.declared_capabilities else defaults & principal.declared_capabilities

    def require_capability(self, principal: KanbanPrincipal, capability: KanbanCapability) -> None:
        if capability not in self.capabilities_for(principal):
            raise KanbanAuthorizationError(f"missing capability: {capability.value}")

    def can_access_scope(
        self,
        principal: KanbanPrincipal,
        scope_kind: str,
        *,
        goal: Any | None = None,
        team: Any | None = None,
    ) -> bool:
        if principal.is_admin or principal.role.lower() == "admin":
            return True
        if scope_kind == "hub":
            return False
        if scope_kind == "team":
            return bool(team and principal.team_id and str(team.id) == principal.team_id)
        if scope_kind != "goal" or goal is None:
            return False
        owner = getattr(goal, "requested_by", None)
        if owner and str(owner) == principal.subject:
            return True
        team_id = getattr(goal, "team_id", None)
        return bool(team_id and principal.team_id and str(team_id) == principal.team_id)

    def require_scope(self, principal: KanbanPrincipal, scope_kind: str, **objects: Any) -> None:
        if not self.can_access_scope(principal, scope_kind, **objects):
            raise KanbanAuthorizationError("board scope is not accessible")

    @staticmethod
    def parse_declared_capabilities(values: Iterable[Any] | None) -> frozenset[KanbanCapability]:
        parsed: set[KanbanCapability] = set()
        for value in values or ():
            try:
                parsed.add(KanbanCapability(str(value)))
            except ValueError:
                pass
        return frozenset(parsed)

