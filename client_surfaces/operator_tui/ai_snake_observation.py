from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

_SENSITIVE_RX = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+")
_SPACE_RX = re.compile(r"\s+")


@dataclass(frozen=True)
class ObservationEvent:
    event_id: str
    timestamp: float
    kind: str
    normalized_value: str
    ref_id: str | None = None


class ObservationBuffer:
    def __init__(self, *, max_events: int = 100) -> None:
        self.max_events = max(10, int(max_events))
        self._events: deque[ObservationEvent] = deque(maxlen=self.max_events)
        self._seq = 0

    def add_event(
        self,
        *,
        kind: str,
        value: Any,
        ref_id: str | None = None,
        timestamp: float | None = None,
    ) -> ObservationEvent:
        normalized = normalize_event_value(kind=kind, value=value)
        self._seq += 1
        event = ObservationEvent(
            event_id=f"ev-{self._seq}",
            timestamp=float(time.time() if timestamp is None else timestamp),
            kind=str(kind).strip().lower() or "unknown",
            normalized_value=normalized,
            ref_id=str(ref_id).strip() if ref_id else None,
        )
        self._events.append(event)
        return event

    def events(self) -> list[ObservationEvent]:
        return list(self._events)

    def compact_summary(self, *, max_facts: int = 20) -> dict[str, Any]:
        max_facts = max(4, int(max_facts))
        events = self.events()
        if not events:
            return {"facts": [], "notes_active": False, "event_count": 0}

        latest_section = ""
        latest_channel = ""
        latest_ref = ""
        latest_command = ""
        movement_window: list[str] = []
        notes_active = False
        facts: list[str] = []

        for event in reversed(events):
            if event.kind == "section" and not latest_section:
                latest_section = event.normalized_value
            elif event.kind == "chat_channel" and not latest_channel:
                latest_channel = event.normalized_value
            elif event.kind in {"artifact", "target_ref"} and not latest_ref:
                latest_ref = event.normalized_value
            elif event.kind == "command" and not latest_command:
                latest_command = event.normalized_value
            elif event.kind == "movement":
                movement_window.append(event.normalized_value)
            elif event.kind == "notes_active":
                notes_active = event.normalized_value == "true"

        if latest_section:
            facts.append(f"section={latest_section}")
        if latest_channel:
            facts.append(f"channel={latest_channel}")
        if latest_ref:
            facts.append(f"selected_ref={latest_ref}")
        if latest_command:
            facts.append(f"last_command={latest_command}")
        if movement_window:
            facts.append(f"movement_trend={_movement_trend(movement_window[:24])}")
        if notes_active:
            facts.append("notes_active=true")

        return {
            "facts": facts[:max_facts],
            "notes_active": notes_active,
            "event_count": len(events),
        }


def normalize_event_value(*, kind: str, value: Any) -> str:
    key = str(kind or "").strip().lower()
    raw = str(value if value is not None else "").strip()
    raw = _SPACE_RX.sub(" ", raw)
    raw = _SENSITIVE_RX.sub(r"\1=[REDACTED]", raw)

    if key == "movement":
        token = raw.lower()
        mapping = {
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "u": "up",
            "d": "down",
            "l": "left",
            "r": "right",
        }
        return mapping.get(token, "idle")
    if key in {"section", "chat_channel"}:
        return re.sub(r"[^a-z0-9:_\-./]", "", raw.lower())[:64] or "unknown"
    if key == "notes_active":
        return "true" if str(value).lower() in {"1", "true", "yes", "on"} else "false"
    if key == "command":
        command = raw[:120]
        command = re.sub(r"(?i)(--?(token|password|secret)\s+\S+)", r"\1 [REDACTED]", command)
        return command
    if key in {"artifact", "target_ref"}:
        return raw[:96]
    return raw[:120]


def _movement_trend(movements: list[str]) -> str:
    if not movements:
        return "idle"
    counts: dict[str, int] = {}
    for move in movements:
        counts[move] = counts.get(move, 0) + 1
    best = max(counts.items(), key=lambda item: item[1])[0]
    if counts.get(best, 0) < max(2, len(movements) // 3):
        return "mixed"
    return best
