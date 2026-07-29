"""Canonical per-call observability records for model invocations."""

from __future__ import annotations

from typing import Any


def build_llm_call_profile_entry(
    *,
    name: str,
    backend: str,
    provider: str | None,
    model: str | None,
    success: bool,
    started_at: float | None,
    ended_at: float | None,
    usage: dict[str, Any] | None = None,
    source: str = "model_invocation_service",
    estimated: bool = False,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build one bounded, JSON-safe invocation profile row."""

    normalized_usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = normalized_usage.get("prompt_tokens")
    completion_tokens = normalized_usage.get("completion_tokens")
    total_tokens = normalized_usage.get("total_tokens")
    latency_ms = None
    if started_at is not None and ended_at is not None:
        latency_ms = max(0, int((ended_at - started_at) * 1000))
    return {
        "name": name,
        "backend": backend,
        "provider": str(provider or "").strip() or None,
        "model": str(model or "").strip() or None,
        "success": bool(success),
        "latency_ms": latency_ms,
        "prompt_tokens": (int(prompt_tokens) if isinstance(prompt_tokens, int) else None),
        "completion_tokens": (int(completion_tokens) if isinstance(completion_tokens, int) else None),
        "total_tokens": (int(total_tokens) if isinstance(total_tokens, int) else None),
        "source": source,
        "estimated": bool(estimated),
        "error_type": str(error_type or "").strip() or None,
        "error_message": str(error_message or "").strip() or None,
        "started_at": (float(started_at) if started_at is not None else None),
        "ended_at": (float(ended_at) if ended_at is not None else None),
    }
