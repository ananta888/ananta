"""Content-free relay telemetry adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.services.semantic_media_observability_policy import sanitize_observability_event


class SemanticRelayObservability:
    def __init__(self, sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._sink = sink or (lambda _event: None)

    def emit(
        self,
        *,
        direction: str,
        traffic_class: str,
        state: str,
        reason_code: str,
        item_count: int = 1,
        duration_ms: float = 0.0,
        scope_digest: str = "",
    ) -> None:
        event = sanitize_observability_event(
            "semantic_transport",
            {
                "direction": direction,
                "transport": f"relay:{traffic_class}",
                "state": state,
                "reason_code": reason_code,
                "item_count": item_count,
                "duration_ms": duration_ms,
                "scope_digest": scope_digest,
            },
        )
        self._sink(event)


__all__ = ["SemanticRelayObservability"]
