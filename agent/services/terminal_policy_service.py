from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from agent.config import settings
from agent.services.sandbox_policy_service import get_sandbox_policy_service


@dataclass(frozen=True)
class TerminalPolicyDecision:
    allow: bool
    reason_code: str
    decision_id: str
    policy_version: str
    matched_rule_id: str | None
    permission: str


class TerminalPolicyService:
    _OP_PERMISSION = {
        "list": "list",
        "create": "create",
        "attach": "attach",
        "read": "read",
        "write": "write",
        "kill": "kill",
    }

    _DEFAULT_ROLE_PERMISSIONS = {
        "admin": {
            "terminal.worker.list",
            "terminal.worker.create",
            "terminal.worker.attach",
            "terminal.worker.read",
            "terminal.worker.write",
            "terminal.worker.kill",
            "terminal.hub.list",
            # hub/hub_as_worker create/attach/read/write/kill stay default-deny
        },
        "user": {
            "terminal.worker.list",
            "terminal.worker.create",
            "terminal.worker.attach",
            "terminal.worker.read",
            "terminal.worker.write",
        },
        "viewer": {
            "terminal.worker.list",
            "terminal.worker.read",
        },
    }

    def _role_permissions(self, cfg: dict[str, Any]) -> dict[str, set[str]]:
        policy_cfg = dict((cfg or {}).get("terminal_policy") or {})
        configured = policy_cfg.get("role_permissions")
        role_permissions = {k: set(v) for k, v in self._DEFAULT_ROLE_PERMISSIONS.items()}
        if isinstance(configured, dict):
            for role, perms in configured.items():
                role_key = str(role or "").strip().lower()
                if not role_key:
                    continue
                entries = set(str(item or "").strip() for item in list(perms or []) if str(item or "").strip())
                role_permissions[role_key] = entries
        return role_permissions

    def _permissions_for_user(self, user_ctx: dict[str, Any], cfg: dict[str, Any]) -> set[str]:
        role_permissions = self._role_permissions(cfg)
        roles: list[str] = []
        role = str(user_ctx.get("role") or "").strip().lower()
        if role:
            roles.append(role)
        for item in list(user_ctx.get("roles") or []):
            candidate = str(item or "").strip().lower()
            if candidate:
                roles.append(candidate)

        effective: set[str] = set()
        for role_name in roles:
            effective.update(role_permissions.get(role_name, set()))

        for explicit in list(user_ctx.get("terminal_permissions") or []):
            permission = str(explicit or "").strip()
            if permission:
                effective.add(permission)

        return effective

    def evaluate(
        self,
        *,
        user_ctx: dict[str, Any],
        operation: str,
        target_type: str,
        target_id: str,
        session_id: str | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> TerminalPolicyDecision:
        policy_version = str(settings.terminal_policy_version or "terminal-policy.v1")
        operation_key = str(operation or "").strip().lower()
        target_key = str(target_type or "").strip().lower()
        action = self._OP_PERMISSION.get(operation_key)
        decision_id = f"term-dec-{uuid.uuid4().hex[:16]}"

        if action is None:
            return TerminalPolicyDecision(
                allow=False,
                reason_code="terminal_operation_unknown",
                decision_id=decision_id,
                policy_version=policy_version,
                matched_rule_id=None,
                permission="",
            )

        permission = f"terminal.{target_key}.{action}"
        app_cfg = dict(cfg or {})
        perms = self._permissions_for_user(user_ctx, app_cfg)
        sandbox_terminal = get_sandbox_policy_service().resolve(app_cfg).get("terminal_access") or {}

        if target_key == "hub" and permission not in perms:
            return TerminalPolicyDecision(
                allow=False,
                reason_code="terminal_hub_access_denied_default",
                decision_id=decision_id,
                policy_version=policy_version,
                matched_rule_id=None,
                permission=permission,
            )
        if bool(sandbox_terminal.get("enforce", True)) and target_key in set(
            str(item or "").strip().lower() for item in list(sandbox_terminal.get("blocked_target_types") or [])
        ):
            return TerminalPolicyDecision(
                allow=False,
                reason_code="terminal_target_blocked_by_sandbox_policy",
                decision_id=decision_id,
                policy_version=policy_version,
                matched_rule_id=None,
                permission=permission,
            )
        write_like = {"create", "attach", "write"}
        admin_gate_targets = set(str(item or "").strip().lower() for item in list(sandbox_terminal.get("write_requires_admin_for") or []))
        role = str(user_ctx.get("role") or "").strip().lower()
        if action in write_like and target_key in admin_gate_targets and role != "admin":
            return TerminalPolicyDecision(
                allow=False,
                reason_code="terminal_sandbox_admin_required",
                decision_id=decision_id,
                policy_version=policy_version,
                matched_rule_id=None,
                permission=permission,
            )

        if permission not in perms:
            return TerminalPolicyDecision(
                allow=False,
                reason_code="terminal_permission_denied",
                decision_id=decision_id,
                policy_version=policy_version,
                matched_rule_id=None,
                permission=permission,
            )

        _ = (session_id, target_id)
        return TerminalPolicyDecision(
            allow=True,
            reason_code="terminal_permission_granted",
            decision_id=decision_id,
            policy_version=policy_version,
            matched_rule_id=f"{target_key}.{action}.allow",
            permission=permission,
        )


_SERVICE = TerminalPolicyService()


def get_terminal_policy_service() -> TerminalPolicyService:
    return _SERVICE
