from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.focus.focus_manager import FocusManager


@dataclass(frozen=True)
class PanelRect:
    x1: int
    y1: int
    x2: int
    y2: int
    focus_id: str
    scroll_context_id: str

    def contains(self, x: int, y: int) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass
class MouseRouter:
    _panels: list[PanelRect] = field(default_factory=list)
    mouse_support_enabled: bool = True

    def register_panel(self, rect: PanelRect) -> None:
        self._panels.append(rect)

    def clear_panels(self) -> None:
        self._panels.clear()

    def scroll_context_at(self, x: int, y: int) -> str | None:
        for panel in reversed(self._panels):
            if panel.contains(x, y):
                return panel.scroll_context_id
        return None

    def focus_id_at(self, x: int, y: int) -> str | None:
        for panel in reversed(self._panels):
            if panel.contains(x, y):
                return panel.focus_id
        return None

    def route_wheel_event(
        self,
        x: int,
        y: int,
        delta: int,
        focus_manager: FocusManager,
        *,
        focus_only: bool = False,
    ) -> str | None:
        """Return the scroll_context_id that should receive this wheel event.

        If focus_only=True, always routes to the focused panel regardless of cursor position.
        If cursor is over a scrollable panel, routes to that panel's context.
        Falls back to the focused panel's context.
        """
        if not self.mouse_support_enabled:
            return focus_manager.active_scroll_context_id()

        if not focus_only:
            ctx_id = self.scroll_context_at(x, y)
            if ctx_id is not None:
                return ctx_id

        return focus_manager.active_scroll_context_id()

    def panels(self) -> list[PanelRect]:
        return list(self._panels)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "panel_count": len(self._panels),
            "mouse_support_enabled": self.mouse_support_enabled,
            "panels": [
                {"focus_id": p.focus_id, "scroll_context_id": p.scroll_context_id,
                 "rect": (p.x1, p.y1, p.x2, p.y2)}
                for p in self._panels
            ],
        }
