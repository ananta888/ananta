from __future__ import annotations

import time
import uuid
from typing import Any

from agent.research_backend import resolve_research_backend_config
from agent.security_risk import RISK_LEVEL_RANK, max_risk_level, normalize_risk_level


TASK_KINDS = {"coding", "analysis", "doc", "ops", "research"}


def normalize_task_kind(task_kind: str | None, prompt: str) -> str:
    if task_kind:
        val = str(task_kind).strip().lower()
        if val in TASK_KINDS:
            return val
    text = (prompt or "").lower()
    if any(k in text for k in ("refactor", "implement", "fix", "code", "test", "bug")):
        return "coding"
    if any(k in text for k in ("deploy", "docker", "restart", "kubernetes", "ops", "infrastructure")):
        return "ops"
    if any(k in text for k in ("readme", "documentation", "docs", "explain")):
        return "doc"
    if any(k in text for k in ("research", "investigate", "compare", "sources", "report", "analyze market")):
        return "research"
    return "analysis"


def runtime_routing_config(agent_cfg: dict | None) -> dict[str, Any]:
    cfg = (agent_cfg or {}).get("sgpt_routing", {}) or {}
    return {
        "policy_version": str(cfg.get("policy_version") or "v3"),
        "default_backend": str(cfg.get("default_backend") or "opencode").strip().lower(),
        "task_kind_backend": cfg.get("task_kind_backend") or {},
    }


def resolve_cli_backend(
    task_kind: str,
    requested_backend: str,
    supported_backends: set[str],
    agent_cfg: dict | None,
    fallback_backend: str = "opencode",
    required_capabilities: list[str] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    backend = str(requested_backend or "auto").strip().lower()
    routing_cfg = runtime_routing_config(agent_cfg)
    if backend != "auto":
        return backend, f"explicit_backend:{backend}", routing_cfg

    normalized_required = [str(item or "").strip().lower() for item in (required_capabilities or []) if str(item or "").strip()]
    research_capability_backend = routing_cfg.get("research_capability_backend") or {}
    if str(task_kind or "").strip().lower() == "research":
        for specialization in ("deep_research", "repo_research", "document_research"):
            mapped = str(research_capability_backend.get(specialization) or "").strip().lower()
            if specialization in normalized_required and mapped in supported_backends:
                return mapped, f"research_capability_policy:{specialization}->{mapped}", routing_cfg
        configured_research_backend = str(resolve_research_backend_config(agent_cfg=agent_cfg).get("provider") or "").strip().lower()
        if configured_research_backend in supported_backends:
            return configured_research_backend, f"research_backend_policy:research->{configured_research_backend}", routing_cfg

    kind_map = routing_cfg.get("task_kind_backend") or {}
    mapped = str(kind_map.get(task_kind) or "").strip().lower()
    if mapped in supported_backends:
        return mapped, f"task_kind_policy:{task_kind}->{mapped}", routing_cfg

    configured = str(routing_cfg.get("default_backend") or fallback_backend).strip().lower()
    if configured in supported_backends:
        return configured, f"default_policy:{configured}", routing_cfg
    return fallback_backend, f"default_policy:{fallback_backend}", routing_cfg


def review_policy(
    agent_cfg: dict | None,
    backend: str | None,
    task_kind: str | None,
    *,
    risk_level: str | None = None,
    uses_terminal: bool = False,
    uses_file_access: bool = False,
) -> dict[str, Any]:
    cfg = (agent_cfg or {}).get("review_policy", {}) or {}
    enabled = bool(cfg.get("enabled", True))
    review_backends = {
        str(x).strip().lower() for x in (cfg.get("research_backends") or ["deerflow", "ananta_research"])
    }
    review_task_kinds = {str(x).strip().lower() for x in (cfg.get("task_kinds") or ["research"])}
    base_required = enabled and str(backend or "").strip().lower() in review_backends and str(task_kind or "").strip().lower() in review_task_kinds

    terminal_risk = normalize_risk_level(str(cfg.get("terminal_risk_level") or "high"), default="high") if uses_terminal else "low"
    file_risk = normalize_risk_level(str(cfg.get("file_access_risk_level") or "medium"), default="medium") if uses_file_access else "low"
    explicit_risk = normalize_risk_level(risk_level or "low", default="low")
    effective_risk = max_risk_level(explicit_risk, terminal_risk, file_risk)
    min_review_level = normalize_risk_level(str(cfg.get("min_risk_level_for_review") or "high"), default="high")
    risk_required = enabled and RISK_LEVEL_RANK.get(effective_risk, 1) >= RISK_LEVEL_RANK.get(min_review_level, 3)
    required = base_required or risk_required
    reason = "review_not_required"
    if base_required:
        reason = "research_backend_review_required"
    elif risk_required:
        reason = f"risk_level_review_required:{effective_risk}"
    return {
        "policy_version": str(cfg.get("policy_version") or "review-v1"),
        "enabled": enabled,
        "required": required,
        "reason": reason,
        "risk_level": effective_risk,
        "uses_terminal": bool(uses_terminal),
        "uses_file_access": bool(uses_file_access),
    }


def evaluate_trigger_precheck(
    agent_cfg: dict | None,
    *,
    source: str,
    payload: dict[str, Any] | None,
    parsed_tasks: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    cfg = (agent_cfg or {}).get("trigger_policy", {}) or {}
    policy_version = str(cfg.get("policy_version") or "trigger-precheck-v1")
    enabled = bool(cfg.get("enabled", True))
    normalized_source = str(source or "").strip().lower()
    risk_map = dict(cfg.get("source_risk_map") or {})
    risk_level = str(risk_map.get(normalized_source) or {
        "generic": "medium",
        "github": "medium",
        "jira": "medium",
        "email": "medium",
        "slack": "low",
    }.get(normalized_source, "high")).strip().lower()
    tasks = list(parsed_tasks or [])
    allowed_sources = {
        str(item or "").strip().lower() for item in (cfg.get("allowed_sources") or []) if str(item or "").strip()
    }
    blocked_sources = {str(item or "").strip().lower() for item in (cfg.get("blocked_sources") or []) if str(item or "").strip()}
    max_tasks_per_event = max(1, min(int(cfg.get("max_tasks_per_event") or 20), 100))

    decision = "approved"
    reason = "approved"
    allowed = True

    if enabled and blocked_sources and normalized_source in blocked_sources:
        allowed = False
        decision = "blocked"
        reason = "blocked_source"
    elif enabled and allowed_sources and normalized_source not in allowed_sources:
        allowed = False
        decision = "blocked"
        reason = "source_not_allowed"
    elif enabled and len(tasks) == 0:
        allowed = False
        decision = "blocked"
        reason = "no_actionable_tasks"
    elif enabled and len(tasks) > max_tasks_per_event:
        allowed = False
        decision = "blocked"
        reason = "too_many_tasks"
    elif enabled and any(not (str((task or {}).get("title") or "").strip() or str((task or {}).get("description") or "").strip()) for task in tasks):
        allowed = False
        decision = "blocked"
        reason = "incomplete_task_payload"
    elif enabled and bool(cfg.get("deny_high_risk", False)) and risk_level in {"high", "critical"}:
        allowed = False
        decision = "blocked"
        reason = "high_risk_source_denied"

    return {
        "policy_version": policy_version,
        "enabled": enabled,
        "allowed": allowed,
        "decision": decision,
        "reason": reason,
        "risk_level": risk_level,
        "source": normalized_source,
        "task_count": len(tasks),
        "max_tasks_per_event": max_tasks_per_event,
        "payload_keys": sorted(list((payload or {}).keys()))[:20],
    }


def build_trace_record(
    *,
    task_id: str | None,
    event_type: str,
    task_kind: str | None,
    backend: str | None,
    routing_reason: str | None,
    policy_version: str | None,
    requested_backend: str | None = None,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id or str(uuid.uuid4()),
        "event_type": event_type,
        "task_id": task_id,
        "task_kind": task_kind,
        "backend": backend,
        "requested_backend": requested_backend,
        "routing_reason": routing_reason,
        "policy_version": policy_version,
        "timestamp": time.time(),
        "metadata": metadata or {},
    }
