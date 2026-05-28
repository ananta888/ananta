from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene


@dataclass(frozen=True)
class RenderContext:
    now: float
    metadata: dict[str, Any] = field(default_factory=dict)


class Renderer(Protocol):
    renderer_id: str

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        ...

