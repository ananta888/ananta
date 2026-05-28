from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements


@dataclass
class RendererDiagnosticsView:
    view_id: str = "renderer_diagnostics"

    def view_requirements(self) -> ViewRequirements:
        return ViewRequirements(
            view_id=self.view_id,
            display_name="Renderer Diagnostics",
            description="Runtime renderer and adapter diagnostics",
            required_render_features=("ansi",),
            optional_runtime_requirements=(),
        )

    def update(self, dt: float, state: dict[str, object]) -> None:
        _ = dt
        _ = state

    def render(self, context: ViewContext) -> RenderScene:
        runtime = context.state.get("runtime_status") if isinstance(context.state.get("runtime_status"), dict) else {}
        renderer = str(runtime.get("active_renderer") or context.state.get("active_renderer") or "-")
        adapter = str(runtime.get("active_adapter") or context.state.get("active_adapter") or "-")
        view = str(runtime.get("active_view") or context.state.get("active_view") or self.view_id)
        fps = str(runtime.get("fps") or context.state.get("fps") or "-")
        fallback = str(runtime.get("fallback_reason") or context.state.get("fallback_reason") or "-")
        nodes: list[dict[str, object]] = [
            {"kind": "label", "text": f"view={view}", "x": 0, "y": 0},
            {"kind": "label", "text": f"renderer={renderer}", "x": 0, "y": 1},
            {"kind": "label", "text": f"adapter={adapter}", "x": 0, "y": 2},
            {"kind": "label", "text": f"fps={fps}", "x": 0, "y": 3},
            {
                "kind": "label",
                "text": f"viewport={context.region.columns}x{context.region.rows}",
                "x": 0,
                "y": 4,
            },
            {
                "kind": "label",
                "text": f"pixels={context.region.pixel_width}x{context.region.pixel_height}",
                "x": 0,
                "y": 5,
            },
            {"kind": "label", "text": f"fallback={fallback}", "x": 0, "y": 6},
        ]
        return RenderScene(
            scene_type="renderer_diagnostics",
            nodes=nodes,
            metadata={"animated": False, "cache_hint": "state_versioned"},
        )
