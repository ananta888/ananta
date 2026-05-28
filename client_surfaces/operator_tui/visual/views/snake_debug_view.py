from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext


@dataclass
class SnakeDebugView:
    view_id: str = "snake_debug_view"

    def update(self, dt: float, state: dict[str, object]) -> None:
        _ = dt
        _ = state

    def render(self, context: ViewContext) -> RenderScene:
        snake_raw = context.state.get("snake") or []
        snake = [
            (int(cell[0]), int(cell[1]))
            for cell in snake_raw
            if isinstance(cell, (list, tuple)) and len(cell) == 2
        ]
        target_raw = context.state.get("target")
        target = (
            (int(target_raw[0]), int(target_raw[1]))
            if isinstance(target_raw, (list, tuple)) and len(target_raw) == 2
            else None
        )
        heuristic = str(context.state.get("selected_heuristic") or "-")
        confidence = float(context.state.get("heuristic_confidence") or 0.0)
        nodes: list[dict[str, object]] = [
            {"kind": "snake_path", "points": snake},
            {"kind": "label", "text": f"heuristic={heuristic}", "x": 0, "y": 0},
            {"kind": "label", "text": f"confidence={confidence:.2f}", "x": 0, "y": 1},
        ]
        if snake:
            nodes.append({"kind": "snake_head", "point": snake[0]})
        if target is not None:
            nodes.append({"kind": "target", "point": target})
        return RenderScene(
            scene_type="snake_debug",
            nodes=nodes,
            metadata={"animated": False, "cache_hint": "state_versioned"},
        )

