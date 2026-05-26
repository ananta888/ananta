from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame


@dataclass(frozen=True, slots=True)
class SceneConfig:
    scene: str = "demo-cube"
    width_px: int = 480
    height_px: int = 270
    t: float = 0.0
    quality: str = "high"


class Offscreen3DRenderer(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def render_scene(self, *, config: SceneConfig) -> PixelFrame: ...
