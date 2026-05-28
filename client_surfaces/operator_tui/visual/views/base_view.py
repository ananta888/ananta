from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


@dataclass(frozen=True)
class ViewContext:
    region: ViewportRegion
    now: float
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ViewRequirements:
    view_id: str
    display_name: str
    description: str
    required_render_features: tuple[str, ...]
    optional_runtime_requirements: tuple[str, ...]


class VisualView(Protocol):
    view_id: str

    def update(self, dt: float, state: dict[str, Any]) -> None:
        ...

    def render(self, context: ViewContext) -> RenderScene:
        ...

