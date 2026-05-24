from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any


def _detect_caller(caller_metadata: dict[str, Any]) -> str:
    source = str(caller_metadata.get("source") or caller_metadata.get("client") or "").strip().lower()
    if "opencode" in source:
        return "opencode"
    if "worker" in source:
        return "ananta_worker"
    if "hub" in source:
        return "hub"
    return "unknown"


@dataclass(frozen=True)
class LlmRequestEnvelope:
    request_id: str
    correlation_id: str | None
    created_at: float
    model: str
    messages: list[dict[str, Any]]
    stream: bool
    caller_type: str
    caller_metadata: dict[str, Any]
    task_metadata: dict[str, Any]
    expected_contract: dict[str, Any]
    raw_payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "model": self.model,
            "messages": list(self.messages),
            "stream": self.stream,
            "caller_type": self.caller_type,
            "caller_metadata": dict(self.caller_metadata),
            "task_metadata": dict(self.task_metadata),
            "expected_contract": dict(self.expected_contract),
            "raw_payload": dict(self.raw_payload),
        }


def build_request_envelope(
    *,
    payload: dict[str, Any],
    headers: dict[str, Any] | None = None,
) -> LlmRequestEnvelope:
    body = dict(payload or {})
    hdrs = {str(k): v for k, v in dict(headers or {}).items()}
    request_id = str(
        body.get("request_id")
        or hdrs.get("X-Request-ID")
        or hdrs.get("x-request-id")
        or f"llmi-{uuid.uuid4()}"
    ).strip()
    correlation_id = str(
        body.get("correlation_id") or hdrs.get("X-Correlation-ID") or hdrs.get("x-correlation-id") or ""
    ).strip() or None

    messages = list(body.get("messages") or [])
    model = str(body.get("model") or "").strip()
    stream = bool(body.get("stream", False))
    caller_metadata = dict(body.get("caller") or {})
    task_metadata = dict(body.get("task") or {})
    expected_contract = dict(body.get("expected_contract") or {"api_shape": "openai_chat_completion"})
    caller_type = _detect_caller(caller_metadata)
    return LlmRequestEnvelope(
        request_id=request_id,
        correlation_id=correlation_id,
        created_at=time.time(),
        model=model,
        messages=messages,
        stream=stream,
        caller_type=caller_type,
        caller_metadata=caller_metadata,
        task_metadata=task_metadata,
        expected_contract=expected_contract,
        raw_payload=body,
    )

