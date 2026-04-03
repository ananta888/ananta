from __future__ import annotations

import time
from typing import Any


def resolve_security_policy(*, agent_config: dict[str, Any], security_level: str) -> dict[str, Any]:
    level = (security_level or "safe").strip().lower()
    if level not in {"safe", "balanced", "aggressive"}:
        level = "safe"

    defaults = {
        "safe": {
            "max_concurrency_cap": 1,
            "execute_timeout": 45,
            "execute_retries": 0,
            "allowed_tool_classes": ["read"],
        },
        "balanced": {
            "max_concurrency_cap": 2,
            "execute_timeout": 60,
            "execute_retries": 1,
            "allowed_tool_classes": ["read", "write"],
        },
        "aggressive": {
            "max_concurrency_cap": 4,
            "execute_timeout": 120,
            "execute_retries": 2,
            "allowed_tool_classes": ["read", "write", "admin", "unknown"],
        },
    }
    policy_cfg = (agent_config or {}).get("autopilot_security_policies", {}) or {}
    configured = policy_cfg.get(level) if isinstance(policy_cfg, dict) else None
    base = {**defaults[level]}
    if isinstance(configured, dict):
        if "max_concurrency_cap" in configured:
            base["max_concurrency_cap"] = max(1, int(configured.get("max_concurrency_cap") or base["max_concurrency_cap"]))
        if "execute_timeout" in configured:
            base["execute_timeout"] = max(1, int(configured.get("execute_timeout") or base["execute_timeout"]))
        if "execute_retries" in configured:
            base["execute_retries"] = max(0, int(configured.get("execute_retries") or 0))
        allowed = configured.get("allowed_tool_classes")
        if isinstance(allowed, list) and allowed:
            allowed_list = [str(item).strip().lower() for item in allowed if str(item).strip()]
            base["allowed_tool_classes"] = allowed_list or defaults[level]["allowed_tool_classes"]
    return {"level": level, **base}


def resolve_guardrail_limits(*, agent_config: dict[str, Any]) -> dict[str, Any]:
    cfg = (agent_config or {}).get("autonomous_guardrails", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "max_runtime_seconds": int(cfg.get("max_runtime_seconds") or 21600),
        "max_ticks_total": int(cfg.get("max_ticks_total") or 5000),
        "max_dispatched_total": int(cfg.get("max_dispatched_total") or 50000),
    }


def check_guardrail_limits(
    *,
    limits: dict[str, Any],
    started_at: float | None,
    tick_count: int,
    dispatched_count: int,
) -> str | None:
    if not limits.get("enabled", True):
        return None
    now = time.time()
    max_runtime_seconds = int(limits.get("max_runtime_seconds") or 0)
    max_ticks_total = int(limits.get("max_ticks_total") or 0)
    max_dispatched_total = int(limits.get("max_dispatched_total") or 0)
    if started_at and max_runtime_seconds > 0 and (now - started_at) >= max_runtime_seconds:
        return "guardrail_max_runtime_seconds_exceeded"
    if max_ticks_total > 0 and tick_count >= max_ticks_total:
        return "guardrail_max_ticks_total_exceeded"
    if max_dispatched_total > 0 and dispatched_count >= max_dispatched_total:
        return "guardrail_max_dispatched_total_exceeded"
    return None


def resolve_resilience_config(*, agent_config: dict[str, Any]) -> dict[str, Any]:
    cfg = (agent_config or {}).get("autonomous_resilience", {}) or {}
    return {
        "retry_attempts": max(1, int(cfg.get("retry_attempts") or 2)),
        "retry_backoff_seconds": max(0.0, float(cfg.get("retry_backoff_seconds") or 0.2)),
        "retry_max_backoff_seconds": max(0.0, float(cfg.get("retry_max_backoff_seconds") or 5.0)),
        "retry_jitter_factor": max(0.0, min(float(cfg.get("retry_jitter_factor") or 0.2), 1.0)),
        "circuit_breaker_threshold": max(1, int(cfg.get("circuit_breaker_threshold") or 3)),
        "circuit_breaker_open_seconds": max(1.0, float(cfg.get("circuit_breaker_open_seconds") or 30.0)),
    }
