"""Fail-closed run ownership boundary for workflow HTTP routes.

This is a containment layer until the signed runtime authorization envelope
and persistent workflow read model are available.  Losing this in-memory
index after a restart denies access instead of exposing an unbound run.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol


@dataclass(frozen=True)
class WorkflowRoutePrincipal:
    tenant_id: str
    subject: str
    roles: tuple[str, ...] = ()

    @classmethod
    def from_auth_context(cls, context: dict[str, object]) -> "WorkflowRoutePrincipal":
        subject = str(context.get("sub") or context.get("username") or "").strip()
        tenant_id = str(context.get("tenant_id") or context.get("tenant") or subject).strip()
        if not subject or not tenant_id:
            raise ValueError("workflow_authenticated_principal_required")
        raw_roles = context.get("roles") or context.get("role") or ()
        if isinstance(raw_roles, str):
            roles = tuple(
                sorted({value.strip() for value in raw_roles.split(",") if value.strip()})
            )
        elif isinstance(raw_roles, (list, tuple, set, frozenset)):
            roles = tuple(sorted({str(value).strip() for value in raw_roles if str(value).strip()}))
        else:
            roles = ()
        return cls(tenant_id=tenant_id, subject=subject, roles=roles)


@dataclass(frozen=True)
class WorkflowRunOwner:
    workflow_id: str
    tenant_id: str
    subject: str

    def is_owned_by(self, principal: WorkflowRoutePrincipal) -> bool:
        return self.tenant_id == principal.tenant_id and self.subject == principal.subject


class WorkflowRunOwnerResolver(Protocol):
    """Read-only restart recovery seam backed by Hub persistence."""

    def resolve(self, workflow_id: str) -> WorkflowRunOwner | None: ...


class WorkflowRouteAuthorizationService:
    """Atomically reserve and check workflow IDs for one authenticated owner."""

    def __init__(self, resolver: WorkflowRunOwnerResolver | None = None) -> None:
        self._owners: dict[str, WorkflowRunOwner] = {}
        self._resolver = resolver
        self._lock = RLock()

    def set_owner_resolver(self, resolver: WorkflowRunOwnerResolver | None) -> None:
        with self._lock:
            self._resolver = resolver

    def reserve(self, workflow_id: str, principal: WorkflowRoutePrincipal) -> str:
        normalized_id = str(workflow_id or "").strip()
        if not normalized_id:
            return "invalid"
        existing = self._owner(normalized_id)
        with self._lock:
            existing = self._owners.get(normalized_id) or existing
            if existing is not None:
                return "duplicate" if existing.is_owned_by(principal) else "foreign"
            self._owners[normalized_id] = WorkflowRunOwner(
                workflow_id=normalized_id,
                tenant_id=principal.tenant_id,
                subject=principal.subject,
            )
        return "reserved"

    def is_authorized(self, workflow_id: str, principal: WorkflowRoutePrincipal) -> bool:
        normalized_id = str(workflow_id or "").strip()
        owner = self._owner(normalized_id)
        return owner is not None and owner.is_owned_by(principal)

    def release(self, workflow_id: str, principal: WorkflowRoutePrincipal) -> None:
        normalized_id = str(workflow_id or "").strip()
        with self._lock:
            owner = self._owners.get(normalized_id)
            if owner is not None and owner.is_owned_by(principal):
                self._owners.pop(normalized_id, None)

    def clear(self) -> None:
        """Test/process lifecycle hook; never broadens authorization."""
        with self._lock:
            self._owners.clear()

    def _owner(self, workflow_id: str) -> WorkflowRunOwner | None:
        with self._lock:
            owner = self._owners.get(workflow_id)
            resolver = self._resolver
        if owner is not None or resolver is None:
            return owner
        resolved = resolver.resolve(workflow_id)
        if resolved is None or resolved.workflow_id != workflow_id:
            return None
        with self._lock:
            return self._owners.setdefault(workflow_id, resolved)


workflow_route_authorization_service = WorkflowRouteAuthorizationService()
