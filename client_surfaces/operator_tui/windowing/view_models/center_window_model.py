from __future__ import annotations

from typing import Any


def build_center_window_model(*, state: Any, game: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": str(game.get("center_window_view_mode") or "simple"),
        "section": str(getattr(state, "section_id", "") or ""),
        "focus": str(getattr(getattr(state, "focus", None), "value", getattr(state, "focus", "")) or ""),
        "status_message": str(getattr(state, "status_message", "") or ""),
        "visual_view": str(game.get("visual_viewport_active_view") or ""),
        "center_browser_active": bool(game.get("center_browser_active")),
    }
