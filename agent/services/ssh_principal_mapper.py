"""Deterministic mapper between Ananta identities/policies and OpenSSH principals.

Deny-by-default: unknown groups and unmapped roles produce no principals.
Worker principals are separate from hub principals.
Broad principals are rejected in production (SSH_BROAD_PRINCIPALS_ALLOWED=false).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent.config import settings

LOGGER = logging.getLogger("agent.ssh_principal_mapper")

_SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

_WORKER_GROUPS = {"ananta-terminal-worker"}
_HUB_GROUPS = {"ananta-terminal-hub"}
_HUB_AS_WORKER_GROUPS = {"ananta-terminal-hub"}  # same permission group; distinct principal

_ALLOWED_TARGET_TYPES = {"worker", "hub", "hub_as_worker"}

_TARGET_PRINCIPAL_PREFIX = {
    "worker": "ananta-worker",
    "hub": "ananta-hub",
    "hub_as_worker": "ananta-hub-as-worker",
}


@dataclass(frozen=True)
class PrincipalMapping:
    principals: tuple[str, ...]
    allowed: bool
    reason_code: str
    target_type: str
    user_id: str


@dataclass
class _MappingContext:
    user_id: str
    groups: list[str]
    target_type: str
    broad_principals_allowed: bool


def _validate_label(value: str) -> bool:
    return bool(_SAFE_LABEL.match(value))


class SshPrincipalMapper:
    """Maps Ananta user identity and target type to a deterministic set of SSH principals."""

    def map(
        self,
        *,
        user_ctx: dict[str, Any],
        target_type: str,
        cfg: dict[str, Any] | None = None,
    ) -> PrincipalMapping:
        user_id = str(user_ctx.get("sub") or user_ctx.get("username") or "").strip()
        if not user_id:
            return PrincipalMapping(
                principals=(),
                allowed=False,
                reason_code="ssh_principal_mapper_missing_user_id",
                target_type=target_type,
                user_id="",
            )

        if target_type not in _ALLOWED_TARGET_TYPES:
            return PrincipalMapping(
                principals=(),
                allowed=False,
                reason_code=f"ssh_principal_mapper_unknown_target_type:{target_type}",
                target_type=target_type,
                user_id=user_id,
            )

        groups: list[str] = []
        raw = user_ctx.get("groups") or []
        if isinstance(raw, list):
            groups = [str(g) for g in raw if g]

        terminal_permissions: set[str] = set(user_ctx.get("terminal_permissions") or [])

        broad_allowed = settings.ssh_broad_principals_allowed

        ctx = _MappingContext(
            user_id=user_id,
            groups=groups,
            target_type=target_type,
            broad_principals_allowed=broad_allowed,
        )
        return self._evaluate(ctx, terminal_permissions)

    def _evaluate(self, ctx: _MappingContext, terminal_permissions: set[str]) -> PrincipalMapping:
        target_type = ctx.target_type

        if target_type == "worker":
            has_worker_group = bool(set(ctx.groups) & _WORKER_GROUPS)
            has_permission = f"terminal.ssh.worker.create" in terminal_permissions or f"terminal.worker.create" in terminal_permissions
            if not (has_worker_group or has_permission):
                return PrincipalMapping(
                    principals=(),
                    allowed=False,
                    reason_code="ssh_principal_mapper_no_worker_permission",
                    target_type=target_type,
                    user_id=ctx.user_id,
                )
            principal = self._build_principal(ctx.user_id, "worker", ctx.broad_principals_allowed)
            if principal is None:
                return PrincipalMapping(
                    principals=(),
                    allowed=False,
                    reason_code="ssh_principal_mapper_broad_principal_rejected",
                    target_type=target_type,
                    user_id=ctx.user_id,
                )
            return PrincipalMapping(
                principals=(principal,),
                allowed=True,
                reason_code="ssh_principal_mapper_ok",
                target_type=target_type,
                user_id=ctx.user_id,
            )

        if target_type == "hub":
            has_hub_group = bool(set(ctx.groups) & _HUB_GROUPS)
            has_permission = "terminal.ssh.hub.create" in terminal_permissions or "terminal.hub.create" in terminal_permissions
            if not (has_hub_group and has_permission):
                return PrincipalMapping(
                    principals=(),
                    allowed=False,
                    reason_code="ssh_principal_mapper_no_hub_permission",
                    target_type=target_type,
                    user_id=ctx.user_id,
                )
            principal = self._build_principal(ctx.user_id, "hub", ctx.broad_principals_allowed)
            if principal is None:
                return PrincipalMapping(
                    principals=(),
                    allowed=False,
                    reason_code="ssh_principal_mapper_broad_principal_rejected",
                    target_type=target_type,
                    user_id=ctx.user_id,
                )
            return PrincipalMapping(
                principals=(principal,),
                allowed=True,
                reason_code="ssh_principal_mapper_ok",
                target_type=target_type,
                user_id=ctx.user_id,
            )

        if target_type == "hub_as_worker":
            has_hub_group = bool(set(ctx.groups) & _HUB_AS_WORKER_GROUPS)
            has_permission = (
                "terminal.ssh.hub_as_worker.create" in terminal_permissions
                or "terminal.hub_as_worker.create" in terminal_permissions
            )
            if not (has_hub_group and has_permission):
                return PrincipalMapping(
                    principals=(),
                    allowed=False,
                    reason_code="ssh_principal_mapper_no_hub_as_worker_permission",
                    target_type=target_type,
                    user_id=ctx.user_id,
                )
            principal = self._build_principal(ctx.user_id, "hub-as-worker", ctx.broad_principals_allowed)
            if principal is None:
                return PrincipalMapping(
                    principals=(),
                    allowed=False,
                    reason_code="ssh_principal_mapper_broad_principal_rejected",
                    target_type=target_type,
                    user_id=ctx.user_id,
                )
            return PrincipalMapping(
                principals=(principal,),
                allowed=True,
                reason_code="ssh_principal_mapper_ok",
                target_type=target_type,
                user_id=ctx.user_id,
            )

        return PrincipalMapping(
            principals=(),
            allowed=False,
            reason_code="ssh_principal_mapper_unhandled_target_type",
            target_type=target_type,
            user_id=ctx.user_id,
        )

    def _build_principal(self, user_id: str, scope: str, broad_allowed: bool) -> str | None:
        # Deterministic scoped principal: ananta-<scope>-<sanitized_user_id>
        # Never return a broad/wildcard principal in production
        sanitized = re.sub(r"[^a-z0-9._-]", "-", user_id.lower())[:40].strip("-")
        if not sanitized or not _validate_label(sanitized):
            sanitized = "user"
        principal = f"ananta-{scope}-{sanitized}"
        if len(principal) > 64:
            principal = principal[:64]
        if not broad_allowed and ("*" in principal or "?" in principal):
            LOGGER.warning("Broad principal rejected: %s", principal)
            return None
        return principal


_INSTANCE: SshPrincipalMapper | None = None


def get_ssh_principal_mapper() -> SshPrincipalMapper:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SshPrincipalMapper()
    return _INSTANCE
