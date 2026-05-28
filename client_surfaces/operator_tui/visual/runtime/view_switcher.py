from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ViewSwitcher:
    _available_view_ids: list[str] = field(default_factory=list)
    _unavailable_view_ids: list[str] = field(default_factory=list)
    _active_view_id: str = ""
    _overlay_visible: bool = False

    def set_views(
        self,
        available: list[str],
        unavailable: list[str] | None = None,
    ) -> None:
        self._available_view_ids = list(available)
        self._unavailable_view_ids = list(unavailable or [])
        if self._active_view_id not in self._available_view_ids:
            self._active_view_id = self._available_view_ids[0] if self._available_view_ids else ""

    def switch_to(self, view_id: str, *, force: bool = False) -> bool:
        if view_id in self._available_view_ids:
            self._active_view_id = view_id
            return True
        if force:
            self._active_view_id = view_id
            return True
        return False

    def next_view(self) -> str:
        if not self._available_view_ids:
            return self._active_view_id
        try:
            idx = self._available_view_ids.index(self._active_view_id)
            self._active_view_id = self._available_view_ids[(idx + 1) % len(self._available_view_ids)]
        except ValueError:
            self._active_view_id = self._available_view_ids[0]
        return self._active_view_id

    def previous_view(self) -> str:
        if not self._available_view_ids:
            return self._active_view_id
        try:
            idx = self._available_view_ids.index(self._active_view_id)
            self._active_view_id = self._available_view_ids[(idx - 1) % len(self._available_view_ids)]
        except ValueError:
            self._active_view_id = self._available_view_ids[-1]
        return self._active_view_id

    def active_view_id(self) -> str:
        return self._active_view_id

    def toggle_overlay(self) -> bool:
        self._overlay_visible = not self._overlay_visible
        return self._overlay_visible

    def set_overlay_visible(self, visible: bool) -> None:
        self._overlay_visible = visible

    def is_overlay_visible(self) -> bool:
        return self._overlay_visible

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "active_view_id": self._active_view_id,
            "available_views": list(self._available_view_ids),
            "unavailable_views": list(self._unavailable_view_ids),
            "overlay_visible": self._overlay_visible,
        }
