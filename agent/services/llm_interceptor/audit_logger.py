from __future__ import annotations

import time
from typing import Any


class AuditLogger:
    """Redacted audit logging for interceptor request lifecycle."""

    def __init__(self, *, debug_prompt_logging: bool = False) -> None:
        self.debug_prompt_logging = bool(debug_prompt_logging)

    def build_event(
        self,
        *,
        request_id: str,
        correlation_id: str | None,
        caller_type: str,
        upstream_id: str,
        model: str,
        policy_decision: dict[str, Any],
        redaction_meta: dict[str, Any],
        duration_ms: int,
        task_metadata: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        task_info = dict(task_metadata or {})
        trace_id = str(correlation_id or request_id or "").strip() or request_id
        context_classes = list(task_info.get("context_classes") or redaction_meta.get("context_classes") or [])
        event = {
            "event_type": "llm_interceptor_request",
            "operation_type": "llm_interaction",
            "request_id": request_id,
            "trace_id": trace_id,
            "task_id": str(task_info.get("task_id") or "").strip() or None,
            "caller_type": caller_type,
            "upstream_id": upstream_id,
            "model": model,
            "target": {"provider_backend": upstream_id, "model": model},
            "policy_version": str((policy_decision or {}).get("policy_version") or "unknown"),
            "prompt_bundle_class": str(task_info.get("prompt_bundle_class") or "unknown"),
            "context_classes": [str(value).strip().lower() for value in context_classes if str(value).strip()],
            "policy_decision": dict(policy_decision or {}),
            "redaction": dict(redaction_meta or {}),
            "duration_ms": int(duration_ms),
            "created_at": int(time.time()),
            "chain": {"parent_trace_id": str(task_info.get("parent_trace_id") or "").strip() or None},
        }
        if self.debug_prompt_logging and messages:
            event["redacted_messages"] = list(messages)
        return event
