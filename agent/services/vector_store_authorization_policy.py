"""Shared authorization policy for Hub-owned Vector administration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.vector_index_task_ingress_policy import (
    find_reserved_vector_index_marker,
)

_ADMIN_ROLES = frozenset({"admin", "superadmin", "system_admin"})
_GLOBAL_ADMIN_ROLES = frozenset({"superadmin", "system_admin"})
_SYSTEM_PURPOSES = frozenset({"goal_purge", "run_control"})
_TRUSTED_SERVICE_AUTH_MODES = frozenset(
    {"agent_jwt", "agent_static_token"}
)


def _normalized_values(
    value: Any,
    *,
    lowercase: bool,
) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        normalized
        for item in value
        for normalized in [
            (
                str(item or "").strip().lower()
                if lowercase
                else str(item or "").strip()
            )
        ]
        if normalized
    )


def vector_task_payload(value: Any) -> dict[str, Any]:
    """Project task-like values onto the fields owned by this policy."""

    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        candidate = value.model_dump()
        return dict(candidate) if isinstance(candidate, Mapping) else {}
    return {
        "id": getattr(value, "id", None),
        "source": getattr(value, "source", None),
        "history": getattr(value, "history", None),
        "task_kind": getattr(value, "task_kind", None),
        "required_capabilities": getattr(
            value,
            "required_capabilities",
            None,
        ),
        "worker_execution_context": getattr(
            value,
            "worker_execution_context",
            None,
        ),
    }


@dataclass(frozen=True, slots=True)
class VectorAdminAuthorizationContext:
    """Immutable authorization facts already established by a trusted caller."""

    actor: str
    roles: frozenset[str]
    workspace_ids: frozenset[str]
    source: str
    system_purpose: str | None = None


class VectorStoreAuthorizationPolicy:
    """Authorize Vector control-plane writes independently from Flask routes."""

    def from_identity(
        self,
        identity: Mapping[str, Any] | None,
        *,
        authenticated_admin: bool = False,
        source: str = "authenticated_request",
    ) -> VectorAdminAuthorizationContext:
        claims = dict(identity or {})
        roles = set(
            _normalized_values(
                claims.get("roles"),
                lowercase=True,
            )
        )
        direct_role = str(claims.get("role") or "").strip().lower()
        if direct_role:
            roles.add(direct_role)
        auth_mode = str(
            claims.get("auth_mode") or ""
        ).strip().lower()
        if (
            authenticated_admin
            and bool(claims)
            and auth_mode in _TRUSTED_SERVICE_AUTH_MODES
        ):
            roles.add("system_admin")

        workspace_ids = set(
            _normalized_values(
                claims.get("workspace_ids"),
                lowercase=False,
            )
        )
        direct_workspace = str(
            claims.get("workspace_id") or ""
        ).strip()
        if direct_workspace:
            workspace_ids.add(direct_workspace)
        actor = str(
            claims.get("sub")
            or claims.get("username")
            or claims.get("service_id")
            or "unknown"
        ).strip()
        return VectorAdminAuthorizationContext(
            actor=actor or "unknown",
            roles=frozenset(roles),
            workspace_ids=frozenset(workspace_ids),
            source=str(source or "authenticated_request"),
        )

    def system_context(
        self,
        *,
        actor: str,
        purpose: str,
    ) -> VectorAdminAuthorizationContext:
        normalized_purpose = str(purpose or "").strip().lower()
        if normalized_purpose not in _SYSTEM_PURPOSES:
            raise ValueError(
                "vector_store_system_authorization_purpose_invalid"
            )
        normalized_actor = str(actor or "").strip()
        if not normalized_actor:
            raise ValueError(
                "vector_store_system_authorization_actor_invalid"
            )
        return VectorAdminAuthorizationContext(
            actor=normalized_actor,
            roles=frozenset({"system_admin"}),
            workspace_ids=frozenset(),
            source="internal_control_plane",
            system_purpose=normalized_purpose,
        )

    @staticmethod
    def require_admin(
        authorization: VectorAdminAuthorizationContext | None,
    ) -> None:
        if (
            authorization is None
            or not authorization.roles.intersection(_ADMIN_ROLES)
        ):
            raise PermissionError("vector_store_admin_required")

    @staticmethod
    def require_global_admin(
        authorization: VectorAdminAuthorizationContext | None,
    ) -> None:
        if (
            authorization is None
            or not authorization.roles.intersection(
                _GLOBAL_ADMIN_ROLES
            )
        ):
            raise PermissionError(
                "vector_store_global_admin_required"
            )

    def require_workspace_admin(
        self,
        authorization: VectorAdminAuthorizationContext | None,
        workspace_id: Any,
    ) -> None:
        self.require_admin(authorization)
        assert authorization is not None
        if authorization.roles.intersection(_GLOBAL_ADMIN_ROLES):
            return
        normalized_workspace = str(workspace_id or "").strip()
        if (
            not normalized_workspace
            or normalized_workspace
            not in authorization.workspace_ids
        ):
            raise PermissionError(
                "vector_store_workspace_forbidden"
            )

    def require_task_admin(
        self,
        authorization: VectorAdminAuthorizationContext | None,
        task: Any,
    ) -> None:
        raw = vector_task_payload(task)
        context = raw.get("worker_execution_context")
        envelope = (
            context.get("vector_index_task")
            if isinstance(context, Mapping)
            else None
        )
        scope = (
            envelope.get("scope")
            if isinstance(envelope, Mapping)
            else None
        )
        workspace_id = (
            scope.get("workspace_id")
            if isinstance(scope, Mapping)
            else None
        )
        self.require_workspace_admin(
            authorization,
            workspace_id,
        )


def has_reserved_vector_index_marker(task: Any) -> bool:
    """Recognize complete and partial reserved Vector domain markers."""

    return reserved_vector_index_marker(task) is not None


def reserved_vector_index_marker(task: Any) -> str | None:
    """Return the shared ingress marker for an existing task row."""

    return find_reserved_vector_index_marker(
        vector_task_payload(task)
    )


_POLICY = VectorStoreAuthorizationPolicy()


def get_vector_store_authorization_policy() -> (
    VectorStoreAuthorizationPolicy
):
    return _POLICY


__all__ = [
    "VectorAdminAuthorizationContext",
    "VectorStoreAuthorizationPolicy",
    "get_vector_store_authorization_policy",
    "has_reserved_vector_index_marker",
    "reserved_vector_index_marker",
    "vector_task_payload",
]
