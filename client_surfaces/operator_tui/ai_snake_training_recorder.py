from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_store import ensure_training_layout


class AiSnakeTrainingRecorder:
    def __init__(
        self,
        *,
        enabled: bool = True,
        max_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.enabled = bool(enabled)
        self.max_bytes = max(64_000, int(max_bytes))
        self.paused = False
        self._paths = ensure_training_layout()
        self._event_queue: list[str] = []   # buffered JSON lines, flushed by background thread

    @property
    def events_path(self) -> Path:
        return self._paths["events_log"]

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)

    def record_event(
        self,
        *,
        event_type: str,
        value_norm: str,
        refs: list[str] | None = None,
        privacy_class: str = "workspace",
        retention_hint: str = "rolling_7d",
    ) -> bool:
        """Queue an event in memory — NO disk I/O. Call flush_queued() from a background thread."""
        if not self.enabled or self.paused:
            return False
        event_name = str(event_type).strip()
        if not event_name:
            return False
        payload: dict[str, Any] = {
            "schema_version": "ai_snake_behavior_event.v1",
            "event_id": f"evt_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            "event_type": event_name,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "context_ref": "operator_tui",
            "target_ref": (refs or [""])[0] if refs else "",
            "value_norm": self._normalize_value(value_norm=value_norm, privacy_class=privacy_class),
            "refs": [str(x).strip() for x in (refs or []) if str(x).strip()][:8],
            "privacy_class": self._normalize_privacy_class(privacy_class),
            "retention_hint": retention_hint if retention_hint in {"ephemeral", "rolling_7d", "rolling_30d"} else "rolling_7d",
            "source": {"component": "operator_tui", "mode": "training"},
            "extensions": {},
        }
        self._event_queue.append(json.dumps(payload, ensure_ascii=False))
        return True

    def flush_queued(self) -> int:
        """Write buffered events to disk. Safe to call from a background thread.

        Returns the number of events flushed. The queue is drained atomically
        so concurrent calls are safe (GIL protects the list swap).
        """
        if not self._event_queue:
            return 0
        pending, self._event_queue = self._event_queue, []
        self._rotate_if_needed()
        try:
            with self.events_path.open("a", encoding="utf-8") as fh:
                for line in pending:
                    fh.write(line + "\n")
        except OSError:
            pass
        return len(pending)

    def _rotate_if_needed(self) -> None:
        path = self.events_path
        if not path.exists():
            return
        if path.stat().st_size < self.max_bytes:
            return
        rotated = path.with_suffix(path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        path.rename(rotated)

    def _normalize_value(self, *, value_norm: str, privacy_class: str) -> str:
        normalized = str(value_norm or "").strip()
        if self._normalize_privacy_class(privacy_class) == "private_local":
            return "notes_active=true" if normalized else "private_event"
        return normalized[:200]

    @staticmethod
    def _normalize_privacy_class(value: str) -> str:
        key = str(value or "").strip()
        if key in {"public_ui", "workspace", "private_local", "sensitive_blocked"}:
            return key
        return "workspace"
