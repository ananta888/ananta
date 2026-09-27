"""Access roles: built-in roles, identity-to-role resolution and effective grants (WCRB-003).

Simple by default, fine-grained when needed:

- Four built-in roles cover the common case: ``viewer`` (read), ``developer``
  (read plus controlled execution such as tests), ``maintainer`` (plus writing
  to repositories and write-class MCP tools) and ``admin`` (everything).
- Custom roles allow or deny single operations or operation groups and can be
  constrained to paths, projects or knowledge indices.
- Bindings attach roles to a local user or to a Keycloak group, realm role or
  client role (``<client>:<role>``), globally or within one tenant/project.

Effective grants are the union of all bound roles; a deny always wins over an
allow. Without any binding, callers keep the previous admin/non-admin
behaviour (the resolver then yields no roles).

Operation groups referenced here are defined by the operation registry;
``ananta.tool.*`` groups cover the worker tools (WCRB-005).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

SUBJECT_KINDS = ("local_user", "oidc_group", "oidc_realm_role", "oidc_client_role")
_ROLE_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_GRANT_LISTS = ("allow_groups", "allow_operations", "deny_groups", "deny_operations")
_CONSTRAINTS = ("paths", "projects", "index_ids")

READ_GROUPS = ["api.read.v1", "mcp.read.v1", "ananta.tool.read.v1"]
BUILTIN_ROLES: dict[str, dict[str, Any]] = {
    "viewer": {
        "name": "Betrachter",
        "description": "Read-only: status, tasks, artifacts, CodeCompass and repository reads.",
        "grants": {"allow_groups": READ_GROUPS},
    },
    "developer": {
        "name": "Entwickler",
        "description": "Viewer plus controlled execution (tests, allowlisted commands); no repository writes.",
        "grants": {"allow_groups": [*READ_GROUPS, "ananta.tool.execution.v1"]},
    },
    "maintainer": {
        "name": "Maintainer",
        "description": "Developer plus repository writes (patches, files) and write-class MCP tools.",
        "grants": {"allow_groups": [*READ_GROUPS, "ananta.tool.execution.v1", "ananta.tool.write.v1",
                                    "mcp.write.v1"]},
    },
    "admin": {
        "name": "Admin",
        "description": "Everything, including administration.",
        "grants": {"admin": True},
    },
}


class AccessRoleError(ValueError):
    """An invalid role, grant or binding."""


# --- grants ------------------------------------------------------------------------------------


def normalize_grants(raw: Any) -> dict[str, Any]:
    """A validated grants document; unknown keys or wrong shapes are errors, not ignored."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise AccessRoleError("grants_must_be_object")
    unknown = set(raw) - {*_GRANT_LISTS, "admin", "constraints"}
    if unknown:
        raise AccessRoleError("grants_unknown_keys:" + ",".join(sorted(unknown)))
    grants: dict[str, Any] = {}
    for key in _GRANT_LISTS:
        values = raw.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise AccessRoleError(f"grants_{key}_must_be_string_list")
        grants[key] = sorted({item.strip() for item in values})
    if "admin" in raw:
        if not isinstance(raw["admin"], bool):
            raise AccessRoleError("grants_admin_must_be_boolean")
        grants["admin"] = raw["admin"]
    constraints = raw.get("constraints")
    if constraints is not None:
        if not isinstance(constraints, Mapping) or set(constraints) - set(_CONSTRAINTS):
            raise AccessRoleError("grants_constraints_invalid")
        grants["constraints"] = {}
        for key in _CONSTRAINTS:
            values = constraints.get(key)
            if values is None:
                continue
            if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
                raise AccessRoleError(f"grants_constraints_{key}_must_be_string_list")
            grants["constraints"][key] = sorted({item.strip() for item in values})
    return grants


@dataclass(frozen=True)
class EffectiveGrants:
    """What a set of roles may do together. ``None`` constraints mean unconstrained."""

    roles: frozenset[str] = frozenset()
    admin: bool = False
    allow_groups: frozenset[str] = frozenset()
    allow_operations: frozenset[str] = frozenset()
    deny_groups: frozenset[str] = frozenset()
    deny_operations: frozenset[str] = frozenset()
    constraints: Mapping[str, frozenset[str] | None] = field(default_factory=dict)

    def allows(self, operation_id: str, groups: Iterable[str]) -> tuple[bool, str]:
        """``(allowed, rule_id)`` for one operation; a deny wins, admin allows everything else."""
        groups = set(groups)
        if operation_id in self.deny_operations:
            return False, f"deny:role:operation:{operation_id}"
        denied = sorted(groups & self.deny_groups)
        if denied:
            return False, f"deny:role:group:{denied[0]}"
        if self.admin:
            return True, "allow:role:admin"
        if operation_id in self.allow_operations:
            return True, f"allow:role:operation:{operation_id}"
        allowed = sorted(groups & self.allow_groups)
        if allowed:
            return True, f"allow:role:group:{allowed[0]}"
        return False, "deny:role:no_grant"


def effective_grants(roles: Mapping[str, Mapping[str, Any]], role_ids: Iterable[str]) -> EffectiveGrants:
    """Union of the grants of ``role_ids`` (unknown ids are skipped). Constraints widen with each role."""
    wanted = [role_id for role_id in sorted(set(role_ids)) if role_id in roles]
    lists: dict[str, set[str]] = {key: set() for key in _GRANT_LISTS}
    admin = False
    constraints: dict[str, set[str] | None] = {}
    for role_id in wanted:
        grants = roles[role_id] or {}
        admin = admin or bool(grants.get("admin"))
        for key in _GRANT_LISTS:
            lists[key].update(grants.get(key) or [])
        role_constraints = grants.get("constraints") or {}
        for key in _CONSTRAINTS:
            # a role without a constraint grants the dimension unconstrained; constrained roles add up
            if key not in role_constraints:
                constraints[key] = None
            elif constraints.get(key, set()) is not None:
                constraints[key] = set(constraints.get(key) or set()) | set(role_constraints[key])
    return EffectiveGrants(
        roles=frozenset(wanted), admin=admin,
        allow_groups=frozenset(lists["allow_groups"]), allow_operations=frozenset(lists["allow_operations"]),
        deny_groups=frozenset(lists["deny_groups"]), deny_operations=frozenset(lists["deny_operations"]),
        constraints={key: (frozenset(value) if value is not None else None) for key, value in constraints.items()},
    )


# --- identity -> roles -------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityClaims:
    """What an identity brings along: its local username and the Keycloak memberships from its token."""

    username: str = ""
    groups: frozenset[str] = frozenset()
    realm_roles: frozenset[str] = frozenset()
    client_roles: frozenset[str] = frozenset()  # "<client>:<role>"
    tenant_id: str | None = None
    project_id: str | None = None

    def subjects(self) -> set[tuple[str, str]]:
        found = {("oidc_group", group) for group in self.groups}
        found |= {("oidc_realm_role", role) for role in self.realm_roles}
        found |= {("oidc_client_role", role) for role in self.client_roles}
        if self.username:
            found.add(("local_user", self.username))
        return found


def normalize_group(value: str) -> str:
    """Keycloak group paths come with or without the leading slash; both bind the same group."""
    value = str(value or "").strip()
    return value if value.startswith("/") or not value else "/" + value


def resolve_role_ids(bindings: Iterable[Mapping[str, Any]], claims: IdentityClaims) -> frozenset[str]:
    """Role ids bound to any subject of ``claims``, honouring tenant/project scoped bindings."""
    subjects = claims.subjects()
    groups = {normalize_group(group) for group in claims.groups}
    matched: set[str] = set()
    for binding in bindings:
        kind, subject = str(binding.get("subject_kind") or ""), str(binding.get("subject") or "")
        subject = normalize_group(subject) if kind == "oidc_group" else subject
        present = subject in groups if kind == "oidc_group" else (kind, subject) in subjects
        if not present:
            continue
        if binding.get("tenant_id") and binding["tenant_id"] != claims.tenant_id:
            continue
        if binding.get("project_id") and binding["project_id"] != claims.project_id:
            continue
        matched.add(str(binding.get("role_id") or ""))
    matched.discard("")
    return frozenset(matched)


def validate_role(role_id: str, name: str, grants: Any) -> tuple[str, str, dict[str, Any]]:
    role_id = str(role_id or "").strip().lower()
    if not _ROLE_ID.match(role_id):
        raise AccessRoleError("role_id_invalid")
    name = str(name or "").strip() or role_id
    return role_id, name[:120], normalize_grants(grants)


def validate_binding(subject_kind: str, subject: str, role_id: str) -> tuple[str, str, str]:
    subject_kind = str(subject_kind or "").strip()
    if subject_kind not in SUBJECT_KINDS:
        raise AccessRoleError("subject_kind_invalid")
    subject = str(subject or "").strip()
    if not subject or len(subject) > 256:
        raise AccessRoleError("subject_invalid")
    if subject_kind == "oidc_group":
        subject = normalize_group(subject)
    if subject_kind == "oidc_client_role" and ":" not in subject:
        raise AccessRoleError("client_role_needs_client_prefix")
    return subject_kind, subject, str(role_id or "").strip().lower()
