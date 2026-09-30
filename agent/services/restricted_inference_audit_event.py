"""Audit event record emitted by the restricted model inference gateway."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InferenceAuditEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    event: str = ""
    operation: str = ""
    task_id: str = ""
    adapter_engine: str = ""
    model_id: str = ""
    manifest_digest: str = ""
    path: str = ""
    latency_ms: float = 0.0
    reason_code: str = ""
    fallback_used: bool = False
    matched_rule: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event": self.event,
            "operation": self.operation,
            "task_id": self.task_id,
            "engine": self.adapter_engine,
            "adapter_engine": self.adapter_engine,
            "model_id": self.model_id,
            "manifest_digest": self.manifest_digest,
            "path": self.path,
            "latency_ms": round(self.latency_ms, 2),
            "reason_code": self.reason_code,
            "fallback_used": self.fallback_used,
            "matched_rule": self.matched_rule,
            "ts": self.ts,
        }


__all__ = ["InferenceAuditEvent"]
