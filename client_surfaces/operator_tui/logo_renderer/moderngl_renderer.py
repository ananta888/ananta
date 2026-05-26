from __future__ import annotations

import math

from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame
from client_surfaces.operator_tui.logo_renderer.renderer_3d import SceneConfig

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency path
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]


class ModernGLOffscreenRenderer:
    """Optional ModernGL offscreen path with deterministic software fallback preview."""

    name = "moderngl"

    def is_available(self) -> bool:
        try:
            import moderngl  # noqa: F401

            return True
        except Exception:
            return False

    def render_scene(self, *, config: SceneConfig) -> PixelFrame:
        width = max(32, int(config.width_px))
        height = max(24, int(config.height_px))
        if Image is None or ImageDraw is None:
            return PixelFrame(width_px=0, height_px=0, rgba=b"", metadata={"renderer": self.name, "ok": False})

        img = Image.new("RGBA", (width, height), (8, 12, 20, 255))
        draw = ImageDraw.Draw(img, "RGBA")
        self._draw_demo_cube(draw=draw, width=width, height=height, t=float(config.t))
        return PixelFrame.from_image(
            img,
            metadata={"renderer": self.name, "scene": config.scene, "ok": True, "offscreen": True, "fallback_sw": not self.is_available()},
        )

    def _draw_demo_cube(self, *, draw, width: int, height: int, t: float) -> None:
        cx = width // 2
        cy = height // 2
        size = max(28, min(width, height) // 5)
        rot = t * 1.8
        ox = int(math.cos(rot) * size * 0.55)
        oy = int(math.sin(rot * 0.7) * size * 0.28)
        z = int(size * 0.65)
        front = [
            (cx - size + ox, cy - size + oy),
            (cx + size + ox, cy - size + oy),
            (cx + size + ox, cy + size + oy),
            (cx - size + ox, cy + size + oy),
        ]
        back = [(x + z, y - z) for (x, y) in front]
        draw.polygon(back, fill=(18, 46, 90, 180))
        draw.polygon(front, fill=(64, 140, 228, 210))
        for i in range(4):
            j = (i + 1) % 4
            draw.line([front[i], front[j]], fill=(190, 230, 255, 255), width=2)
            draw.line([back[i], back[j]], fill=(120, 170, 230, 220), width=2)
            draw.line([front[i], back[i]], fill=(120, 210, 160, 220), width=2)
