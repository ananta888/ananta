from __future__ import annotations

import json
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


@dataclass
class _GuardrailConfig:
    max_calls: int
    max_external: int
    max_cost_units: int
    max_tokens: int
    chars_per_token: int
    class_limits: dict[str, int]
    class_cost_units: dict[str, int]
    tool_classes: dict[str, str]
    blocked_classes: set[str]
    external_classes: set[str]


def _extract_guardrail_config(guard: dict) -> _GuardrailConfig:
    return _GuardrailConfig(
        max_calls=int(guard.get("max_tool_calls_per_request") or 5),
        max_external=int(guard.get("max_external_calls_per_request") or 2),
        max_cost_units=int(guard.get("max_estimated_cost_units_per_request") or 20),
        max_tokens=int(guard.get("max_tokens_per_request") or 0),
        chars_per_token=int(guard.get("chars_per_token_estimate") or 4),
        class_limits=guard.get("class_limits", {}) or {},
        class_cost_units=guard.get("class_cost_units", {}) or {},
        tool_classes={**DEFAULT_TOOL_CLASSES, **(guard.get("tool_classes", {}) or {})},
        blocked_classes=set(guard.get("blocked_classes", []) or []),
        external_classes=set(guard.get("external_classes", ["write", "admin"]) or ["write", "admin"]),
    )


def _check_token_limits(
    token_usage: dict[str, Any] | None, calls: list[dict], chars_per_token: int, max_tokens: int
) -> tuple[list[str], list[str]]:
    if max_tokens <= 0:
        return [], []

    tool_calls_tokens = estimate_tool_calls_tokens(calls, chars_per_token=chars_per_token)
    prompt_tokens = int((token_usage or {}).get("prompt_tokens") or 0)
    history_tokens = int((token_usage or {}).get("history_tokens") or 0)
    completion_tokens = int((token_usage or {}).get("completion_tokens") or 0)
    explicit_total_tokens = (token_usage or {}).get("estimated_total_tokens")
    estimated_total = (
        int(explicit_total_tokens)
        if explicit_total_tokens is not None
        else (prompt_tokens + history_tokens + completion_tokens + tool_calls_tokens)
    )

    if estimated_total > max_tokens:
        blocked = [str(tc.get("name") or "<missing>") for tc in calls]
        return blocked, ["guardrail_max_estimated_tokens_exceeded"]
    return [], []


def estimate_text_tokens(text: Any, chars_per_token: int = 4) -> int:
    val = str(text or "")
    if not val:
        return 0
    divisor = max(1, int(chars_per_token or 4))
    return max(1, len(val) // divisor)


def estimate_tool_calls_tokens(tool_calls: list[dict] | None, chars_per_token: int = 4) -> int:
    try:
        payload = json.dumps(tool_calls or [], ensure_ascii=False, sort_keys=True)
    except Exception:
        payload = str(tool_calls or [])
    return estimate_text_tokens(payload, chars_per_token=chars_per_token)


def evaluate_tool_call_guardrails(
    tool_calls: list[dict] | None, cfg: dict | None, token_usage: dict[str, Any] | None = None
) -> ToolGuardrailDecision:
    guard = (cfg or {}).get("llm_tool_guardrails", {}) or {}
    if not guard.get("enabled", True):
        return ToolGuardrailDecision(True, [], [], {"enabled": False})

    calls = [tc for tc in (tool_calls or []) if isinstance(tc, dict)]
    if not calls:
        return ToolGuardrailDecision(True, [], [], {"enabled": True, "calls": 0})

    config = _extract_guardrail_config(guard)
    counts_by_class: dict[str, int] = {}
    estimated_cost = 0
    external_calls = 0
    blocked: list[str] = []
    reasons: list[str] = []

    if config.max_calls > 0 and len(calls) > config.max_calls:
        reasons.append("guardrail_max_tool_calls_exceeded")

    for tc in calls:
        name = str(tc.get("name") or "<missing>")
        klass = str(config.tool_classes.get(name, "unknown"))
        counts_by_class[klass] = counts_by_class.get(klass, 0) + 1

        estimated_cost += int(config.class_cost_units.get(klass, config.class_cost_units.get("unknown", 3)))
        if klass in config.external_classes:
            external_calls += 1

        class_limit = int(config.class_limits.get(klass, 0) or 0)
        if class_limit > 0 and counts_by_class[klass] > class_limit:
            blocked.append(name)
            reasons.append(f"guardrail_class_limit_exceeded:{klass}")
        if klass in config.blocked_classes:
            blocked.append(name)
            reasons.append(f"guardrail_class_blocked:{klass}")

    if config.max_external > 0 and external_calls > config.max_external:
        reasons.append("guardrail_max_external_calls_exceeded")
        blocked.extend([str(tc.get("name") or "<missing>") for tc in calls])

    if config.max_cost_units > 0 and estimated_cost > config.max_cost_units:
        reasons.append("guardrail_max_estimated_cost_exceeded")
        blocked.extend([str(tc.get("name") or "<missing>") for tc in calls])

    token_blocked, token_reasons = _check_token_limits(token_usage, calls, config.chars_per_token, config.max_tokens)
    blocked.extend(token_blocked)
    reasons.extend(token_reasons)

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
            "max_tool_calls_per_request": config.max_calls,
            "max_external_calls_per_request": config.max_external,
            "max_estimated_cost_units_per_request": config.max_cost_units,
            "max_tokens_per_request": config.max_tokens,
        },
    )
