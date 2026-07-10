from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flask import current_app, g, has_app_context, has_request_context

from agent.services.ops_models import DANGEROUS_ACTIONS, MUTATING_ACTIONS, READ_ACTIONS


@dataclass(frozen=True)
class OpsPolicyDecision:
    decision: str
    reason_code: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class OpsPolicyService:
    def evaluate(self, tool_name: str, action: str, *, target_id: str = "") -> OpsPolicyDecision:
        cfg = self._config()
        read_actions = set(cfg.get("read_actions") or READ_ACTIONS)
        mutating_actions = set(cfg.get("mutating_actions") or MUTATING_ACTIONS)
        dangerous_actions = set(cfg.get("dangerous_actions") or DANGEROUS_ACTIONS)
        action = str(action or "").strip()
        metadata = {"tool_name": tool_name, "action": action, "target_id": target_id}
        if action in dangerous_actions:
            return OpsPolicyDecision("policy_denied", "policy_denied", metadata)
        if action in read_actions:
            return OpsPolicyDecision("allow", "allowed_read_action", metadata)
        if action in mutating_actions:
            mode = str(cfg.get("mutating_default") or "approval_required")
            if mode == "allow":
                return OpsPolicyDecision("allow", "allowed_mutating_action", metadata)
            if mode == "deny":
                return OpsPolicyDecision("policy_denied", "policy_denied", metadata)
            return OpsPolicyDecision("approval_required", "approval_required", metadata)
        return OpsPolicyDecision("policy_denied", "unknown_action", metadata)

    def _config(self) -> dict[str, Any]:
        if not has_app_context():
            return {}
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        return dict(agent_cfg.get("ops_policy") or {})

    def create_approval_request(
        self,
        *,
        tool_name: str,
        action: str,
        target_id: str,
        arguments: dict[str, Any],
    ) -> str | None:
        try:
            from agent.services.approval_request_service import get_approval_request_service

            task_id = None
            goal_id = None
            trace_id = None
            if has_request_context():
                task_id = str(getattr(g, "task_id", "") or "") or None
                goal_id = str(getattr(g, "goal_id", "") or "") or None
                trace_id = str(getattr(g, "trace_id", "") or "") or None
            request = get_approval_request_service().create_pending_request(
                task_id=task_id,
                goal_id=goal_id,
                trace_id=trace_id,
                tool_name=tool_name,
                arguments=arguments,
                target_fingerprint=str(target_id or ""),
                risk_class="high",
                scope={"approval_class": "controlled_workspace_writes", "ops_action": action},
            )
            return str(request.id)
        except Exception:
            return None


_default_policy: OpsPolicyService | None = None


def get_ops_policy_service() -> OpsPolicyService:
    global _default_policy
    if _default_policy is None:
        _default_policy = OpsPolicyService()
    return _default_policy
