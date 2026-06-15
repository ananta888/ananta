"""Data models for the Visual Guide Engine."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VisualGuideRequest:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    snake_id: str = ""
    trigger_type: str = "ui_tick"  # "ui_tick" | "region_explain" | "route_change" | "manual"
    route: str = ""
    snapshot: str = ""
    region_steps: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "snake_id": self.snake_id,
            "trigger_type": self.trigger_type,
            "route": self.route,
            "snapshot": self.snapshot[:500],
            "region_steps_count": len(self.region_steps),
            "created_at": self.created_at,
        }


@dataclass
class VisualGuideDecision:
    request_id: str = ""
    strategy: str = "llm"  # "llm" | "rule" | "fallback" | "suppressed"
    confidence: float = 0.0
    reason: str = ""
    model_used: str = ""
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "strategy": self.strategy,
            "confidence": self.confidence,
            "reason": self.reason,
            "model_used": self.model_used,
            "fallback_used": self.fallback_used,
        }


@dataclass
class VisualGuideAction:
    request_id: str = ""
    guide_steps: list[dict] = field(default_factory=list)
    trigger_type: str = ""
    priority: int = 5  # 1=highest, 10=lowest; region_explain=2, predictive=7
    ttl_seconds: float = 30.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "guide_steps": self.guide_steps,
            "trigger_type": self.trigger_type,
            "priority": self.priority,
            "ttl_seconds": self.ttl_seconds,
            "created_at": self.created_at,
        }


@dataclass
class VisualGuideTraceEvent:
    event: str = ""  # "request_received" | "snapshot_normalized" | "delta_computed" |
    #                   "decision_started" | "model_invoked" | "action_generated" |
    #                   "action_sent" | "fallback_used" | "suppressed_by_rate_limit" | "error"
    request_id: str = ""
    ts: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "request_id": self.request_id,
            "ts": self.ts,
            "data": self.data,
        }
