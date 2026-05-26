from __future__ import annotations

import math

from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame
from client_surfaces.operator_tui.logo_renderer.renderer_3d import SceneConfig

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency path
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]


class RaylibPrototypeRenderer:
    """Optional prototype renderer; remains non-mandatory for installation."""

    name = "raylib"

    def is_available(self) -> bool:
        try:
            import pyray  # noqa: F401

            return True
        except Exception:
            return False

    def render_scene(self, *, config: SceneConfig) -> PixelFrame:
        width = max(32, int(config.width_px))
        height = max(24, int(config.height_px))
        if Image is None or ImageDraw is None:
            return PixelFrame(width_px=0, height_px=0, rgba=b"", metadata={"renderer": self.name, "ok": False})
        img = Image.new("RGBA", (width, height), (10, 10, 14, 255))
        draw = ImageDraw.Draw(img, "RGBA")
        self._draw_orbit(draw=draw, width=width, height=height, t=float(config.t))
        return PixelFrame.from_image(
            img,
            metadata={"renderer": self.name, "scene": config.scene, "ok": True, "offscreen": True, "fallback_sw": not self.is_available()},
        )

    def _draw_orbit(self, *, draw, width: int, height: int, t: float) -> None:
        cx = width // 2
        cy = height // 2
        r = max(24, min(width, height) // 3)
        draw.ellipse((cx - r, cy - r // 3, cx + r, cy + r // 3), outline=(90, 110, 150, 180), width=2)
        px = cx + int(math.cos(t * 2.2) * r * 0.9)
        py = cy + int(math.sin(t * 2.2) * r * 0.28)
        draw.ellipse((px - 16, py - 16, px + 16, py + 16), fill=(95, 205, 140, 240), outline=(200, 255, 220, 255), width=2)
