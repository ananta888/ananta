from __future__ import annotations

from typing import Any


def build_ai_snake_window_model(game: dict[str, Any]) -> dict[str, Any]:
    return {
        "active": bool(game.get("snake_mode")),
        "paused": bool(game.get("paused")),
        "tutorial_mode": bool(game.get("tutorial_mode")),
        "runtime_status": str(game.get("ai_snake_runtime_status") or "idle"),
        "mode": str(game.get("ai_snake_mode") or "lurking_follow"),
        "heuristic_id": str(game.get("selected_heuristic_id") or ""),
        "heuristic_confidence": float(game.get("heuristic_confidence") or 0.0),
    }
