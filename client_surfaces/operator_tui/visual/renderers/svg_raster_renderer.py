from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene

try:
    import cairosvg  # type: ignore
except Exception:  # pragma: no cover
    cairosvg = None

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None


@dataclass
class SvgRasterRenderer:
    renderer_id: str = "svg_raster_optional"
    max_width: int = 1280
    max_height: int = 720
    allow_external_paths: bool = False

    def _placeholder_png(self, *, width: int, height: int, message: str) -> bytes:
        w = max(1, min(int(width), self.max_width))
        h = max(1, min(int(height), self.max_height))
        if Image is None or ImageDraw is None:
            return bytes([42, 42, 42, 255]) * (w * h)
        image = Image.new("RGBA", (w, h), color=(36, 36, 36, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, w - 1, h - 1), outline=(150, 150, 150, 255))
        draw.text((8, 8), "SVG disabled/fallback", fill=(255, 220, 140, 255))
        draw.text((8, 24), message[:120], fill=(220, 220, 220, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def _load_svg_text(self, scene: RenderScene) -> str:
        direct = str(scene.metadata.get("svg_text") or "").strip()
        if direct:
            return direct
        candidate = str(scene.metadata.get("svg_path") or "").strip()
        if not candidate:
            return ""
        path = Path(candidate).expanduser().resolve()
        if not self.allow_external_paths:
            cwd = Path.cwd().resolve()
            try:
                path.relative_to(cwd)
            except ValueError:
                return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        start = time.perf_counter()
        w = max(1, min(int(width), self.max_width))
        h = max(1, min(int(height), self.max_height))
        svg_text = self._load_svg_text(scene)
        metadata = {
            "renderer": self.renderer_id,
            "scene_type": scene.scene_type,
            "animated": bool(scene.metadata.get("animated")),
        }
        if cairosvg is None or not svg_text:
            payload = self._placeholder_png(width=w, height=h, message="svg unavailable")
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            metadata.update({"generation_ms": round(elapsed_ms, 4), "degraded": True})
            return RenderFrame(
                frame_type="raster",
                width=w,
                height=h,
                payload=payload,
                mime_or_format="image/png",
                timestamp=context.now,
                metadata=metadata,
            )

        try:
            png_bytes = cairosvg.svg2png(
                bytestring=svg_text.encode("utf-8"),
                output_width=w,
                output_height=h,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            metadata.update({"generation_ms": round(elapsed_ms, 4), "degraded": False, "output_bytes": len(png_bytes)})
            return RenderFrame(
                frame_type="raster",
                width=w,
                height=h,
                payload=png_bytes,
                mime_or_format="image/png",
                timestamp=context.now,
                metadata=metadata,
            )
        except Exception as exc:
            payload = self._placeholder_png(width=w, height=h, message=f"svg error: {exc}")
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            metadata.update({"generation_ms": round(elapsed_ms, 4), "degraded": True, "error": str(exc)})
            return RenderFrame(
                frame_type="raster",
                width=w,
                height=h,
                payload=payload,
                mime_or_format="image/png",
                timestamp=context.now,
                metadata=metadata,
            )

