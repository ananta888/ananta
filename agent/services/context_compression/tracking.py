"""HCCA-015: Structured tracking events for compression steps.

Events are emitted to Python logging (structured JSON) and kept in an
in-memory ring-buffer for later inspection.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.services.context_compression import CompressionRequest, CompressionResult

_LOG = logging.getLogger("ananta.compression")


@dataclass(frozen=True)
class CompressionEvent:
    event_type: str  # "compression_attempted" | "compression_completed" | "compression_blocked" | "compression_failed"
    content_type: str
    content_id: str
    decision: str
    reason_code: str
    token_before: int
    token_after: int
    token_delta: int
    quality_score: float
    adapter_used: str
    elapsed_ms: float
    timestamp: float
    task_intent: str = ""
    ccr_ref: str = ""
    diagnostics: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "content_type": self.content_type,
            "content_id": self.content_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "token_before": self.token_before,
            "token_after": self.token_after,
            "token_delta": self.token_delta,
            "quality_score": self.quality_score,
            "adapter_used": self.adapter_used,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": self.timestamp,
            "task_intent": self.task_intent,
            "ccr_ref": self.ccr_ref,
            "diagnostics": self.diagnostics,
        }


class _NoopTracker:
    """A tracker that does nothing (for disabled mode)."""

    def record(self, result: Any, request: Any) -> CompressionEvent:
        return CompressionEvent(
            event_type="compression_completed",
            content_type=getattr(request, "content_type", ""),
            content_id="",
            decision=getattr(result, "decision", "passthrough"),
            reason_code=getattr(result, "reason_code", ""),
            token_before=getattr(result, "token_before", 0),
            token_after=getattr(result, "token_after", 0),
            token_delta=0,
            quality_score=0.0,
            adapter_used="",
            elapsed_ms=0.0,
            timestamp=time.time(),
        )

    def recent_events(self, n: int = 20) -> list[CompressionEvent]:
        return []

    def summary(self) -> dict[str, Any]:
        return {
            "total_requests": 0,
            "total_compressed": 0,
            "total_blocked": 0,
            "total_token_savings": 0,
            "avg_quality_score": 0.0,
        }


class CompressionTracker:
    _MAX_EVENTS = 1000

    def __init__(
        self,
        emit_to_log: bool = True,
        include_in_tracking_viewer: bool = True,
    ) -> None:
        self._emit_to_log = emit_to_log
        self._include_in_tracking_viewer = include_in_tracking_viewer
        self._events: deque[CompressionEvent] = deque(maxlen=self._MAX_EVENTS)

    def record(
        self,
        result: CompressionResult,
        request: CompressionRequest,
    ) -> CompressionEvent:
        """Create a CompressionEvent from result + request, log it, and store it."""
        token_before = getattr(result, "token_before", 0)
        token_after = getattr(result, "token_after", 0)
        decision = getattr(result, "decision", "passthrough")
        reason_code = getattr(result, "reason_code", "")
        quality_score = float(getattr(result, "quality_score", 0.0))
        adapter_used = getattr(result, "adapter_used", "")
        elapsed_ms = float(getattr(result, "elapsed_ms", 0.0))
        ccr_ref = getattr(result, "ccr_ref", "")
        diagnostics = dict(getattr(result, "diagnostics", {}) or {})

        content_type = getattr(request, "content_type", "")
        content_id = getattr(request, "content_id", "")
        task_intent = getattr(request, "task_intent", "")

        if decision in ("compressed", "truncated"):
            event_type = "compression_completed"
        elif decision == "blocked":
            event_type = "compression_blocked"
        elif decision in ("error", "failed"):
            event_type = "compression_failed"
        else:
            event_type = "compression_attempted"

        event = CompressionEvent(
            event_type=event_type,
            content_type=content_type,
            content_id=content_id,
            decision=decision,
            reason_code=reason_code,
            token_before=token_before,
            token_after=token_after,
            token_delta=token_before - token_after,
            quality_score=quality_score,
            adapter_used=adapter_used,
            elapsed_ms=elapsed_ms,
            timestamp=time.time(),
            task_intent=task_intent,
            ccr_ref=ccr_ref,
            diagnostics=diagnostics,
        )

        if self._include_in_tracking_viewer:
            self._events.append(event)

        if self._emit_to_log:
            _LOG.info(json.dumps(event.as_dict()))

        return event

    def recent_events(self, n: int = 20) -> list[CompressionEvent]:
        """Return the most recent n events (newest last)."""
        events = list(self._events)
        return events[-n:]

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics over all recorded events."""
        events = list(self._events)
        total_requests = len(events)
        total_compressed = sum(
            1 for e in events if e.decision in ("compressed", "truncated")
        )
        total_blocked = sum(1 for e in events if e.decision == "blocked")
        total_token_savings = sum(e.token_delta for e in events if e.token_delta > 0)
        quality_scores = [e.quality_score for e in events if e.quality_score > 0]
        avg_quality_score = (
            sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        )
        return {
            "total_requests": total_requests,
            "total_compressed": total_compressed,
            "total_blocked": total_blocked,
            "total_token_savings": total_token_savings,
            "avg_quality_score": avg_quality_score,
        }

    @classmethod
    def noop(cls) -> CompressionTracker:
        """Return a no-op tracker that does nothing (for disabled mode)."""
        # Return a _NoopTracker wrapped to match the CompressionTracker interface.
        # We use a cast via __new__ + swap to avoid subclassing issues.
        instance = object.__new__(cls)
        instance.__class__ = _NoopTracker  # type: ignore[assignment]
        return instance  # type: ignore[return-value]
