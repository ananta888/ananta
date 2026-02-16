from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_TOOL_CLASSES = {
    "list_teams": "read",
    "list_roles": "read",
    "list_agents": "read",
    "list_templates": "read",
    "analyze_logs": "read",
    "read_agent_logs": "read",
    "create_team": "write",
    "assign_role": "write",
    "ensure_team_templates": "write",
    "create_template": "write",
    "update_template": "write",
    "delete_template": "write",
    "update_config": "admin",
}


@dataclass
class ToolGuardrailDecision:
    allowed: bool
    blocked_tools: list[str]
    reasons: list[str]
    details: dict[str, Any]


def evaluate_tool_call_guardrails(tool_calls: list[dict] | None, cfg: dict | None) -> ToolGuardrailDecision:
    guard = (cfg or {}).get("llm_tool_guardrails", {}) or {}
    if not guard.get("enabled", True):
        return ToolGuardrailDecision(True, [], [], {"enabled": False})

    calls = [tc for tc in (tool_calls or []) if isinstance(tc, dict)]
    if not calls:
        return ToolGuardrailDecision(True, [], [], {"enabled": True, "calls": 0})

    max_calls = int(guard.get("max_tool_calls_per_request") or 5)
    max_external = int(guard.get("max_external_calls_per_request") or 2)
    max_cost_units = int(guard.get("max_estimated_cost_units_per_request") or 20)
    class_limits = guard.get("class_limits", {}) or {}
    class_cost_units = guard.get("class_cost_units", {}) or {}
    tool_classes = {**DEFAULT_TOOL_CLASSES, **(guard.get("tool_classes", {}) or {})}
    blocked_classes = set(guard.get("blocked_classes", []) or [])
    external_classes = set(guard.get("external_classes", ["write", "admin"]) or ["write", "admin"])

    counts_by_class: dict[str, int] = {}
    estimated_cost = 0
    external_calls = 0
    blocked: list[str] = []
    reasons: list[str] = []

    if max_calls > 0 and len(calls) > max_calls:
        reasons.append("guardrail_max_tool_calls_exceeded")

    for tc in calls:
        name = str(tc.get("name") or "<missing>")
        klass = str(tool_classes.get(name, "unknown"))
        counts_by_class[klass] = counts_by_class.get(klass, 0) + 1

        estimated_cost += int(class_cost_units.get(klass, class_cost_units.get("unknown", 3)))
        if klass in external_classes:
            external_calls += 1

        class_limit = int(class_limits.get(klass, 0) or 0)
        if class_limit > 0 and counts_by_class[klass] > class_limit:
            blocked.append(name)
            reasons.append(f"guardrail_class_limit_exceeded:{klass}")
        if klass in blocked_classes:
            blocked.append(name)
            reasons.append(f"guardrail_class_blocked:{klass}")

    if max_external > 0 and external_calls > max_external:
        reasons.append("guardrail_max_external_calls_exceeded")
        blocked.extend([str(tc.get("name") or "<missing>") for tc in calls])

    if max_cost_units > 0 and estimated_cost > max_cost_units:
        reasons.append("guardrail_max_estimated_cost_exceeded")
        blocked.extend([str(tc.get("name") or "<missing>") for tc in calls])

    blocked_unique = list(dict.fromkeys(blocked))
    reasons_unique = list(dict.fromkeys(reasons))
    return ToolGuardrailDecision(
        allowed=not reasons_unique,
        blocked_tools=blocked_unique,
        reasons=reasons_unique,
        details={
            "enabled": True,
            "calls": len(calls),
            "counts_by_class": counts_by_class,
            "external_calls": external_calls,
            "estimated_cost_units": estimated_cost,
            "max_tool_calls_per_request": max_calls,
            "max_external_calls_per_request": max_external,
            "max_estimated_cost_units_per_request": max_cost_units,
        },
    )
