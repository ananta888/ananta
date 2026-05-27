"""HeuristicEventBus — normalized event pub/sub with ring-buffer history.

Event types: pointer_move | focus_change | panel_switch | artifact_select |
             chat_message | error_detected

No raw file contents or sensitive data are stored in events — only
normalized string values (hashed or truncated to 200 chars).
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

_VALID_EVENT_TYPES = frozenset({
    "pointer_move",
    "focus_change",
    "panel_switch",
    "artifact_select",
    "artifact_hover",
    "chat_message",
    "error_detected",
    "build_error",
    "goal_change",
    "task_change",
})

_RING_BUFFER_DEFAULT = 100


@dataclass
class HeuristicEvent:
    event_type: str
    surface: str
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    normalized_value: str = ""
    ref_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "surface": self.surface,
            "timestamp": self.timestamp,
            "normalized_value": self.normalized_value[:200],
            "ref_id": self.ref_id,
            "metadata": self.metadata,
        }


Handler = Callable[[HeuristicEvent], None]


class HeuristicEventBus:
    """Thread-safe-ish pub/sub bus with ring-buffer for recent events."""

    def __init__(self, ring_buffer_size: int = _RING_BUFFER_DEFAULT) -> None:
        self._handlers: list[Handler] = []
        self._ring: deque[HeuristicEvent] = deque(maxlen=ring_buffer_size)

    def subscribe(self, handler: Handler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass

    def publish(self, event: HeuristicEvent) -> None:
        self._ring.append(event)
        for handler in list(self._handlers):
            try:
                handler(event)
            except Exception:
                pass  # bus never propagates subscriber exceptions

    def get_recent(self, n: int) -> list[HeuristicEvent]:
        """Return up to n most-recent events (newest last)."""
        events = list(self._ring)
        return events[-n:] if n < len(events) else events

    def clear(self) -> None:
        self._ring.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._handlers)


# ── Adapters ──────────────────────────────────────────────────────────────────

class TuiEventSourceAdapter:
    """Adapts raw TUI events from interactive.py to normalized HeuristicEvents."""

    _KIND_MAP: dict[str, str] = {
        "cursor_move":     "pointer_move",
        "panel_focus":     "focus_change",
        "panel_switch":    "panel_switch",
        "artifact_click":  "artifact_select",
        "artifact_hover":  "artifact_hover",
        "chat_input":      "chat_message",
        "error_event":     "error_detected",
        "build_failed":    "build_error",
        "goal_activated":  "goal_change",
        "task_activated":  "task_change",
    }

    def adapt(self, tui_event: dict[str, Any]) -> HeuristicEvent | None:
        raw_kind = str(tui_event.get("kind") or "").strip().lower()
        event_type = self._KIND_MAP.get(raw_kind, raw_kind)
        if not event_type:
            return None

        normalized = str(tui_event.get("value") or tui_event.get("label") or "")[:200]
        ref_id = str(tui_event.get("ref_id") or tui_event.get("artifact_id") or "") or None

        return HeuristicEvent(
            event_type=event_type,
            surface="tui_snake",
            timestamp=float(tui_event.get("timestamp") or time.time()),
            normalized_value=normalized,
            ref_id=ref_id,
            metadata={k: v for k, v in tui_event.items() if k not in ("kind", "value", "label", "ref_id", "timestamp")},
        )


class EclipseEventSourceAdapter:
    """Adapts AnantaSnakeState change events (from Hub API or direct) to HeuristicEvents."""

    _STATE_MAP: dict[str, str] = {
        "FOLLOWING":   "focus_change",
        "LURKING":     "focus_change",
        "ZONE_CHANGE": "panel_switch",
        "ERROR":       "error_detected",
    }

    def adapt(self, java_event: dict[str, Any]) -> HeuristicEvent | None:
        snake_state = str(java_event.get("snakeState") or java_event.get("state") or "").upper()
        event_type = self._STATE_MAP.get(snake_state, "focus_change")
        zone = str(java_event.get("zone") or "").strip()

        return HeuristicEvent(
            event_type=event_type,
            surface="eclipse_snake",
            timestamp=float(java_event.get("timestamp") or time.time()),
            normalized_value=zone[:200],
            ref_id=str(java_event.get("editorId") or "") or None,
            metadata={"snake_state": snake_state},
        )


# ── Singleton ─────────────────────────────────────────────────────────────────

_BUS: HeuristicEventBus | None = None


def get_event_bus() -> HeuristicEventBus:
    global _BUS
    if _BUS is None:
        _BUS = HeuristicEventBus()
    return _BUS
