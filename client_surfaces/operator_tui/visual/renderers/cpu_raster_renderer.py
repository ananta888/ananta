from __future__ import annotations

import io
import time
from dataclasses import dataclass

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None


def _clamp(value: int, maximum: int) -> int:
    return max(1, min(int(value), int(maximum)))


@dataclass
class CpuRasterRenderer:
    renderer_id: str = "cpu_raster"
    max_width: int = 1280
    max_height: int = 720

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        start = time.perf_counter()
        w = _clamp(width, self.max_width)
        h = _clamp(height, self.max_height)

        if Image is None or ImageDraw is None:
            payload = {
                "width": w,
                "height": h,
                "pixels": bytes([12, 20, 28, 255]) * (w * h),
                "mode": "RGBA",
            }
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return RenderFrame(
                frame_type="raster",
                width=w,
                height=h,
                payload=payload,
                mime_or_format="application/x-rgba",
                timestamp=context.now,
                metadata={
                    "renderer": self.renderer_id,
                    "scene_type": scene.scene_type,
                    "generation_ms": round(elapsed_ms, 4),
                    "animated": bool(scene.metadata.get("animated")),
                    "degraded": True,
                },
            )

        image = Image.new("RGBA", (w, h), color=(12, 20, 28, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, w - 1, h - 1), outline=(80, 100, 120, 255))
        draw.text((8, 8), f"{scene.scene_type}", fill=(240, 240, 240, 255))
        y = 24
        for node in scene.nodes[:20]:
            if not isinstance(node, dict):
                continue
            text = str(node.get("text") or node.get("kind") or "-")
            draw.text((8, y), text[:120], fill=(200, 210, 220, 255))
            y += 14
            if y >= h - 12:
                break
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png = buf.getvalue()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return RenderFrame(
            frame_type="raster",
            width=w,
            height=h,
            payload=png,
            mime_or_format="image/png",
            timestamp=context.now,
            metadata={
                "renderer": self.renderer_id,
                "scene_type": scene.scene_type,
                "generation_ms": round(elapsed_ms, 4),
                "animated": bool(scene.metadata.get("animated")),
                "output_bytes": len(png),
            },
        )

