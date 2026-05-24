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
        caller_type: str,
        upstream_id: str,
        model: str,
        policy_decision: dict[str, Any],
        redaction_meta: dict[str, Any],
        duration_ms: int,
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_type": "llm_interceptor_request",
            "request_id": request_id,
            "caller_type": caller_type,
            "upstream_id": upstream_id,
            "model": model,
            "policy_decision": dict(policy_decision or {}),
            "redaction": dict(redaction_meta or {}),
            "duration_ms": int(duration_ms),
            "created_at": int(time.time()),
        }
        if self.debug_prompt_logging and messages:
            event["redacted_messages"] = list(messages)
        return event

