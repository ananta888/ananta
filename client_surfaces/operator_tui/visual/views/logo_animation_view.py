from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext


@dataclass
class LogoAnimationView:
    view_id: str = "logo_animation"
    _phase: float = 0.0

    def update(self, dt: float, state: dict[str, object]) -> None:
        if bool(state.get("paused")):
            return
        self._phase += max(0.0, float(dt))

    def render(self, context: ViewContext) -> RenderScene:
        spinner = ("|", "/", "-", "\\")
        idx = int(self._phase * 8.0) % len(spinner)
        title = f"ANANTA {spinner[idx]}"
        nodes = [
            {"kind": "title", "text": title, "x": 0, "y": 0},
            {
                "kind": "logo_block",
                "text": "[ANANTA]",
                "x": max(0, (context.region.columns // 2) - 4),
                "y": max(1, context.region.rows // 2),
            },
        ]
        return RenderScene(
            scene_type="logo_animation",
            nodes=nodes,
            metadata={"animated": True, "phase": round(self._phase, 4)},
        )

