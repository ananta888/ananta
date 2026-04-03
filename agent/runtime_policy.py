from __future__ import annotations

import time
import uuid
from typing import Any

from agent.research_backend import resolve_research_backend_config


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
        "default_backend": str(cfg.get("default_backend") or "sgpt").strip().lower(),
        "task_kind_backend": cfg.get("task_kind_backend") or {},
    }


def resolve_cli_backend(
    task_kind: str,
    requested_backend: str,
    supported_backends: set[str],
    agent_cfg: dict | None,
    fallback_backend: str = "sgpt",
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


def review_policy(agent_cfg: dict | None, backend: str | None, task_kind: str | None) -> dict[str, Any]:
    cfg = (agent_cfg or {}).get("review_policy", {}) or {}
    enabled = bool(cfg.get("enabled", True))
    review_backends = {
        str(x).strip().lower() for x in (cfg.get("research_backends") or ["deerflow", "ananta_research"])
    }
    review_task_kinds = {str(x).strip().lower() for x in (cfg.get("task_kinds") or ["research"])}
    required = enabled and str(backend or "").strip().lower() in review_backends and str(task_kind or "").strip().lower() in review_task_kinds
    return {
        "policy_version": str(cfg.get("policy_version") or "review-v1"),
        "enabled": enabled,
        "required": required,
        "reason": "research_backend_review_required" if required else "review_not_required",
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
