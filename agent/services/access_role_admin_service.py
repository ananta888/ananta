"""Administration of access roles and bindings, and resolution of an identity's grants (WCRB-003).

Every change is validated, audited and stored as a full-snapshot revision, so
any earlier state can be restored (``rollback``). Built-in roles are owned by
the code: they are (re)created on first use and cannot be changed or deleted
through the API; custom roles cover everything else.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from agent.services.access_roles import (
    BUILTIN_ROLES,
    AccessRoleError,
    EffectiveGrants,
    IdentityClaims,
    effective_grants,
    resolve_role_ids,
    validate_binding,
    validate_role,
)

_CACHE_SECONDS = 10.0


def _role_row(role: Any) -> dict[str, Any]:
    return {"id": role.id, "name": role.name, "description": role.description, "builtin": bool(role.builtin),
            "grants": dict(role.grants or {})}


def _binding_row(binding: Any) -> dict[str, Any]:
    return {"id": binding.id, "subject_kind": binding.subject_kind, "subject": binding.subject,
            "role_id": binding.role_id, "tenant_id": binding.tenant_id, "project_id": binding.project_id,
            "created_at": binding.created_at, "created_by": binding.created_by}


class AccessRoleAdminService:
    def __init__(self, repository: Any, *, audit: Callable[[str, dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._repo = repository
        self._audit = audit or (lambda _event, _details: None)
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: tuple[float, dict[str, dict[str, Any]], list[dict[str, Any]]] | None = None
        self._seeded = False

    # --- reading -------------------------------------------------------------------------------

    def ensure_builtin_roles(self) -> None:
        """Create or refresh the code-owned built-in roles."""
        if self._seeded:
            return
        from agent.db_models import AccessRoleDB

        for role_id, spec in BUILTIN_ROLES.items():
            current = self._repo.get_role(role_id)
            if current is None or not current.builtin or dict(current.grants or {}) != spec["grants"]:
                self._repo.save_role(AccessRoleDB(id=role_id, name=spec["name"], description=spec["description"],
                                                  builtin=True, grants=spec["grants"]))
        self._seeded = True

    def snapshot(self) -> dict[str, Any]:
        self.ensure_builtin_roles()
        return {"roles": [_role_row(role) for role in self._repo.list_roles()],
                "bindings": [_binding_row(binding) for binding in self._repo.list_bindings()]}

    def _current(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        with self._lock:
            if self._cache is None or self._clock() - self._cache[0] > _CACHE_SECONDS:
                snapshot = self.snapshot()
                roles = {row["id"]: row["grants"] for row in snapshot["roles"]}
                self._cache = (self._clock(), roles, snapshot["bindings"])
            return self._cache[1], self._cache[2]

    def grants_for(self, claims: IdentityClaims) -> EffectiveGrants:
        """The effective grants of an identity (empty when nothing is bound to it)."""
        roles, bindings = self._current()
        return effective_grants(roles, resolve_role_ids(bindings, claims))

    def role_grants(self) -> dict[str, dict[str, Any]]:
        """The grants of every role by id (cached like the bindings)."""
        return dict(self._current()[0])

    def has_bindings(self) -> bool:
        return bool(self._current()[1])

    # --- changing ------------------------------------------------------------------------------

    def _changed(self, actor: str, reason: str, details: dict[str, Any]) -> int:
        with self._lock:
            self._cache = None
        revision = self._repo.add_revision(self.snapshot(), actor=actor, reason=reason)
        self._audit("access_policy_changed", {**details, "revision": revision.revision, "actor": actor,
                                              "reason": reason})
        return revision.revision

    def save_role(self, role_id: str, *, name: str, description: str = "", grants: Any, actor: str) -> int:
        from agent.db_models import AccessRoleDB

        self.ensure_builtin_roles()
        role_id, name, grants = validate_role(role_id, name, grants)
        if role_id in BUILTIN_ROLES:
            raise AccessRoleError("builtin_role_is_read_only")
        self._repo.save_role(AccessRoleDB(id=role_id, name=name, description=str(description or "")[:500],
                                          builtin=False, grants=grants))
        return self._changed(actor, "role_saved", {"role_id": role_id})

    def delete_role(self, role_id: str, *, actor: str) -> int:
        self.ensure_builtin_roles()
        if role_id in BUILTIN_ROLES:
            raise AccessRoleError("builtin_role_is_read_only")
        if not self._repo.delete_role(role_id):
            raise AccessRoleError("role_not_found")
        return self._changed(actor, "role_deleted", {"role_id": role_id})

    def add_binding(self, subject_kind: str, subject: str, role_id: str, *, actor: str,
                    tenant_id: str | None = None, project_id: str | None = None) -> tuple[str, int]:
        from agent.db_models import AccessRoleBindingDB

        self.ensure_builtin_roles()
        subject_kind, subject, role_id = validate_binding(subject_kind, subject, role_id)
        if self._repo.get_role(role_id) is None:
            raise AccessRoleError("role_not_found")
        for existing in self._repo.list_bindings():
            if (existing.subject_kind, existing.subject, existing.role_id, existing.tenant_id,
                    existing.project_id) == (subject_kind, subject, role_id, tenant_id or None, project_id or None):
                raise AccessRoleError("binding_exists")
        binding = self._repo.save_binding(AccessRoleBindingDB(
            subject_kind=subject_kind, subject=subject, role_id=role_id, tenant_id=tenant_id or None,
            project_id=project_id or None, created_by=actor))
        revision = self._changed(actor, "binding_added", {"binding_id": binding.id, "subject_kind": subject_kind,
                                                          "subject": subject, "role_id": role_id})
        return binding.id, revision

    def delete_binding(self, binding_id: str, *, actor: str) -> int:
        if not self._repo.delete_binding(binding_id):
            raise AccessRoleError("binding_not_found")
        return self._changed(actor, "binding_deleted", {"binding_id": binding_id})

    def revisions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{"revision": row.revision, "actor": row.actor, "reason": row.reason, "created_at": row.created_at,
                 "roles": len(row.snapshot.get("roles") or []), "bindings": len(row.snapshot.get("bindings") or [])}
                for row in self._repo.list_revisions(limit)]

    def rollback(self, revision: int, *, actor: str) -> int:
        """Restore the roles and bindings of ``revision``; the restore itself becomes a new revision."""
        row = self._repo.get_revision(int(revision))
        if row is None:
            raise AccessRoleError("revision_not_found")
        snapshot = dict(row.snapshot or {})
        self._repo.replace_all(list(snapshot.get("roles") or []), list(snapshot.get("bindings") or []))
        self._seeded = False
        return self._changed(actor, f"rollback_to_{row.revision}", {"restored_revision": row.revision})


_SERVICE: AccessRoleAdminService | None = None


def get_access_role_admin_service() -> AccessRoleAdminService:
    global _SERVICE
    if _SERVICE is None:
        from agent.common.audit import log_audit
        from agent.repositories.access_roles import AccessRoleRepository

        _SERVICE = AccessRoleAdminService(AccessRoleRepository(), audit=log_audit)
    return _SERVICE
