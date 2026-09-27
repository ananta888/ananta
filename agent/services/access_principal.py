"""One principal for Hub decisions: who is asking, and what their bound roles grant (WCRB-002).

Built only from the authenticated request context (never from query or body):
the subject, tenant and project, the role names carried by the token, the
Keycloak memberships (groups, realm roles, client roles) and the effective
grants of the access roles bound to that identity.

Keycloak memberships are read in two shapes: as Ananta puts them into the Hub
token at login (``idp_groups``, ``idp_realm_roles``, ``idp_client_roles``),
and as a raw Keycloak token carries them (``groups``, ``realm_access.roles``,
``resource_access.<client>.roles``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.services.access_roles import EffectiveGrants, IdentityClaims


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.replace(",", " ").split() if item]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def token_roles(context: Mapping[str, Any], *, is_admin: bool) -> frozenset[str]:
    """Role names carried by the token itself (``role``, ``roles``, ``realm_access.roles``), normalized."""
    roles: set[str] = set()
    for raw in (context.get("role"), context.get("roles")):
        roles.update(_strings(raw))
    realm_access = context.get("realm_access")
    if isinstance(realm_access, Mapping):
        roles.update(_strings(realm_access.get("roles")))
    normalized = {item.lower().replace("-", "_") for item in roles}
    if is_admin:
        normalized.add("admin")
    return frozenset(normalized)


def identity_claims(context: Mapping[str, Any]) -> IdentityClaims:
    """The identity the access-role bindings match against."""
    groups = set(_strings(context.get("idp_groups"))) | set(_strings(context.get("groups")))
    realm_roles = set(_strings(context.get("idp_realm_roles")))
    realm_access = context.get("realm_access")
    if isinstance(realm_access, Mapping):
        realm_roles |= set(_strings(realm_access.get("roles")))
    client_roles = set(_strings(context.get("idp_client_roles")))
    resource_access = context.get("resource_access")
    if isinstance(resource_access, Mapping):
        for client, access in resource_access.items():
            if isinstance(access, Mapping):
                client_roles |= {f"{client}:{role}" for role in _strings(access.get("roles"))}
    username = str(context.get("sub") or context.get("username") or "").strip()
    return IdentityClaims(
        username="" if username == "agent_token" else username,
        groups=frozenset(groups), realm_roles=frozenset(realm_roles), client_roles=frozenset(client_roles),
        tenant_id=str(context.get("tenant_id") or context.get("tenant") or context.get("tid") or "").strip() or None,
        project_id=str(context.get("project_id") or context.get("project") or context.get("pid") or "").strip()
        or None,
    )


@dataclass(frozen=True)
class AccessPrincipal:
    subject_id: str
    tenant_id: str | None
    project_id: str | None
    auth_source: str  # agent_token | user_jwt | worker | pre_authenticated_context | anonymous
    is_admin: bool  # the legacy admin flag of the request
    token_roles: frozenset[str] = frozenset()
    claims: IdentityClaims = field(default_factory=IdentityClaims)
    grants: EffectiveGrants = field(default_factory=EffectiveGrants)

    @property
    def roles(self) -> frozenset[str]:
        """Access roles bound to this identity."""
        return self.grants.roles

    def as_dict(self) -> dict[str, Any]:
        return {"subject_id": self.subject_id, "tenant_id": self.tenant_id, "project_id": self.project_id,
                "auth_source": self.auth_source, "is_admin": self.is_admin, "token_roles": sorted(self.token_roles),
                "access_roles": sorted(self.roles), "groups": sorted(self.claims.groups),
                "realm_roles": sorted(self.claims.realm_roles), "client_roles": sorted(self.claims.client_roles),
                "grants": {"admin": self.grants.admin, "allow_groups": sorted(self.grants.allow_groups),
                           "allow_operations": sorted(self.grants.allow_operations),
                           "deny_groups": sorted(self.grants.deny_groups),
                           "deny_operations": sorted(self.grants.deny_operations),
                           "constraints": {key: (sorted(value) if value is not None else None)
                                           for key, value in self.grants.constraints.items()}}}


def with_memberships(claims: IdentityClaims, memberships: Mapping[str, Any] | None) -> IdentityClaims:
    """``claims`` plus stored Keycloak memberships (groups, realm roles, client roles)."""
    memberships = memberships or {}
    return IdentityClaims(
        username=claims.username, tenant_id=claims.tenant_id, project_id=claims.project_id,
        groups=claims.groups | frozenset(_strings(memberships.get("groups"))),
        realm_roles=claims.realm_roles | frozenset(_strings(memberships.get("realm_roles"))),
        client_roles=claims.client_roles | frozenset(_strings(memberships.get("client_roles"))),
    )


def build_access_principal(context: Mapping[str, Any], *, is_admin: bool, auth_source: str,
                           grants_for: Callable[[IdentityClaims], EffectiveGrants] | None = None,
                           memberships_for: Callable[[str], Mapping[str, Any]] | None = None) -> AccessPrincipal:
    roles = token_roles(context, is_admin=is_admin)
    claims = identity_claims(context)
    if memberships_for is not None and claims.username:
        claims = with_memberships(claims, memberships_for(claims.username))
    subject_id = str(context.get("sub") or context.get("subject_id") or context.get("user_id")
                     or context.get("username") or ("hub_admin" if "admin" in roles else "")).strip()
    grants = grants_for(claims) if grants_for is not None else EffectiveGrants()
    return AccessPrincipal(subject_id=subject_id, tenant_id=claims.tenant_id, project_id=claims.project_id,
                           auth_source=auth_source, is_admin=is_admin, token_roles=roles, claims=claims,
                           grants=grants)
