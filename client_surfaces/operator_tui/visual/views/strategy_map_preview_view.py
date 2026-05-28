from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements


@dataclass
class StrategyMapPreviewView:
    view_id: str = "strategy_map_preview"

    def view_requirements(self) -> ViewRequirements:
        return ViewRequirements(
            view_id=self.view_id,
            display_name="Strategy Map",
            description="Territory overview strategy map",
            required_render_features=("ansi",),
            optional_runtime_requirements=(),
        )

    def update(self, dt: float, state: dict[str, object]) -> None:
        _ = dt
        _ = state

    def render(self, context: ViewContext) -> RenderScene:
        territories_raw = context.state.get("territories")
        territories = [
            dict(item)
            for item in territories_raw
            if isinstance(item, dict)
        ] if isinstance(territories_raw, list) else []
        selected = str(context.state.get("selected_territory") or "").strip()
        zoom = float(context.state.get("zoom") or 1.0)
        nodes: list[dict[str, object]] = [
            {"kind": "label", "text": f"strategy_map zoom={zoom:.2f}", "x": 0, "y": 0},
        ]
        for idx, territory in enumerate(territories[:24]):
            tid = str(territory.get("id") or f"T{idx + 1}")
            owner = str(territory.get("owner") or "-")
            x = int(territory.get("x") or 0)
            y = int(territory.get("y") or 0)
            nodes.append(
                {
                    "kind": "territory",
                    "id": tid,
                    "owner": owner,
                    "point": (x, y),
                    "selected": tid == selected,
                }
            )
        if selected:
            nodes.append({"kind": "label", "text": f"selected={selected}", "x": 0, "y": 1})
        return RenderScene(
            scene_type="strategy_map_preview",
            nodes=nodes,
            metadata={"animated": False, "cache_hint": "state_versioned"},
        )

