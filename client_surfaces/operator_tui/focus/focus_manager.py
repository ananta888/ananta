from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_FOCUS_ORDER = [
    "nav_panel",
    "main_content",
    "detail_panel",
    "artifact_panel",
    "ai_panel",
    "chat_panel",
    "center_viewport",
    "log_panel",
]


@dataclass
class FocusManager:
    _active_focus_id: str = "main_content"
    _scroll_context_map: dict[str, str] = field(default_factory=dict)
    _focus_order: list[str] = field(default_factory=lambda: list(_DEFAULT_FOCUS_ORDER))

    def set_active(self, focus_id: str) -> None:
        self._active_focus_id = str(focus_id)

    def active(self) -> str:
        return self._active_focus_id

    def active_scroll_context_id(self) -> str | None:
        return self._scroll_context_map.get(self._active_focus_id)

    def register_scroll_context(self, focus_id: str, scroll_context_id: str) -> None:
        self._scroll_context_map[focus_id] = scroll_context_id

    def deregister_scroll_context(self, focus_id: str) -> None:
        self._scroll_context_map.pop(focus_id, None)

    def cycle_next(self, order: list[str] | None = None) -> str:
        seq = order or self._focus_order
        focusable = [fid for fid in seq if fid in self._scroll_context_map or fid in seq]
        if not focusable:
            return self._active_focus_id
        try:
            idx = focusable.index(self._active_focus_id)
            self._active_focus_id = focusable[(idx + 1) % len(focusable)]
        except ValueError:
            self._active_focus_id = focusable[0]
        return self._active_focus_id

    def cycle_previous(self, order: list[str] | None = None) -> str:
        seq = order or self._focus_order
        focusable = [fid for fid in seq if fid in self._scroll_context_map or fid in seq]
        if not focusable:
            return self._active_focus_id
        try:
            idx = focusable.index(self._active_focus_id)
            self._active_focus_id = focusable[(idx - 1) % len(focusable)]
        except ValueError:
            self._active_focus_id = focusable[-1]
        return self._active_focus_id

    def set_focus_order(self, order: list[str]) -> None:
        self._focus_order = list(order)

    def all_scroll_context_ids(self) -> list[str]:
        return list(self._scroll_context_map.values())

    def diagnostics(self) -> dict[str, Any]:
        return {
            "active_focus_id": self._active_focus_id,
            "active_scroll_context_id": self.active_scroll_context_id(),
            "registered": dict(self._scroll_context_map),
            "focus_order": list(self._focus_order),
        }
