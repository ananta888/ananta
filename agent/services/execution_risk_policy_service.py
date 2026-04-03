from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.security_risk import (
    RISK_LEVEL_RANK,
    classify_command_risk,
    classify_tool_calls_risk,
    has_file_access_signal,
    has_terminal_signal,
    max_risk_level,
    normalize_risk_level,
)


@dataclass(frozen=True)
class ExecutionRiskDecision:
    allowed: bool
    review_required: bool
    risk_level: str
    reasons: list[str]
    blocked_tools: list[str]
    details: dict[str, Any]


def _policy(agent_cfg: dict | None) -> dict[str, Any]:
    cfg = (agent_cfg or {}).get("execution_risk_policy", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "default_action": str(cfg.get("default_action") or "deny").strip().lower(),
        "deny_risk_levels": [normalize_risk_level(item, "high") for item in (cfg.get("deny_risk_levels") or ["high", "critical"])],
        "review_risk_levels": [normalize_risk_level(item, "medium") for item in (cfg.get("review_risk_levels") or ["medium", "high", "critical"])],
        "task_scoped_only": bool(cfg.get("task_scoped_only", True)),
        "require_terminal_capability_for_command": bool(cfg.get("require_terminal_capability_for_command", True)),
        "terminal_capability_name": str(cfg.get("terminal_capability_name") or "terminal").strip().lower(),
        "deny_tool_call_risk": bool(cfg.get("deny_tool_call_risk", False)),
    }


def evaluate_execution_risk(
    *,
    command: str | None,
    tool_calls: list[dict] | None,
    task: dict | None,
    agent_cfg: dict | None,
) -> ExecutionRiskDecision:
    policy = _policy(agent_cfg)
    if not policy["enabled"]:
        return ExecutionRiskDecision(True, False, "low", [], [], {"enabled": False})

    worker_ctx = dict((task or {}).get("worker_execution_context") or {})
    is_task_scoped = bool(worker_ctx)
    if policy["task_scoped_only"] and not is_task_scoped:
        return ExecutionRiskDecision(True, False, "low", [], [], {"enabled": True, "task_scoped": False})

    command_risk = classify_command_risk(command)
    tools_risk = classify_tool_calls_risk(tool_calls, guard_cfg=agent_cfg or {})
    risk_level = max_risk_level(command_risk, tools_risk)
    reasons: list[str] = []
    blocked_tools = [str(item.get("name") or "<missing>") for item in (tool_calls or []) if isinstance(item, dict)]

    required_caps = {str(item or "").strip().lower() for item in (task or {}).get("required_capabilities") or [] if str(item or "").strip()}
    if command and policy["require_terminal_capability_for_command"]:
        terminal_capability = policy["terminal_capability_name"]
        if terminal_capability not in required_caps:
            reasons.append("terminal_capability_required")
            risk_level = max_risk_level(risk_level, "high")

    deny_levels = set(policy["deny_risk_levels"])
    review_levels = set(policy["review_risk_levels"])
    review_required = risk_level in review_levels
    deny_for_risk = (bool(command) or policy["deny_tool_call_risk"]) and risk_level in deny_levels
    allowed = not deny_for_risk and not reasons

    if not allowed:
        reasons.append(f"execution_risk_denied:{risk_level}")

    if not allowed and policy["default_action"] == "allow":
        allowed = True
        reasons = [item for item in reasons if not item.startswith("execution_risk_denied:")]

    return ExecutionRiskDecision(
        allowed=allowed,
        review_required=review_required,
        risk_level=risk_level,
        reasons=list(dict.fromkeys(reasons)),
        blocked_tools=blocked_tools,
        details={
            "enabled": True,
            "task_scoped": is_task_scoped,
            "risk_level": risk_level,
            "command_risk": command_risk,
            "tool_calls_risk": tools_risk,
            "uses_terminal": has_terminal_signal(command),
            "uses_file_access": has_file_access_signal(command, tool_calls),
            "required_capabilities": sorted(required_caps),
            "policy": policy,
        },
    )
