from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene

try:
    import cairosvg  # type: ignore
    _CAIROSVG_AVAILABLE = True
except Exception:  # pragma: no cover
    cairosvg = None
    _CAIROSVG_AVAILABLE = False

try:
    from PIL import Image, ImageDraw
    _PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    _PIL_AVAILABLE = False


@dataclass
class SvgRasterRenderer:
    renderer_id: str = "svg_raster_optional"
    max_width: int = 1280
    max_height: int = 720
    allow_external_paths: bool = False

    def _placeholder_png(self, *, width: int, height: int, message: str) -> bytes:
        w = max(1, min(int(width), self.max_width))
        h = max(1, min(int(height), self.max_height))
        if not _PIL_AVAILABLE:
            return bytes([42, 42, 42, 255]) * (w * h)
        image = Image.new("RGBA", (w, h), color=(36, 36, 36, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, w - 1, h - 1), outline=(150, 150, 150, 255))
        draw.text((8, 8), "SVG disabled/fallback", fill=(255, 220, 140, 255))
        draw.text((8, 24), message[:120], fill=(220, 220, 220, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def _svg_to_png(self, svg_text: str, *, width: int, height: int) -> bytes | None:
        if not _CAIROSVG_AVAILABLE or not svg_text:
            return None
        try:
            return cairosvg.svg2png(
                bytestring=svg_text.encode("utf-8"),
                output_width=width,
                output_height=height,
            )
        except Exception:
            return None

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

    def _render_diagram_nodes(
        self, diagram_nodes: list[dict[str, Any]], *, width: int, height: int
    ) -> bytes | None:
        """Render first SVG diagram_image node to PNG (MDP-011 / MIMG-008)."""
        if not diagram_nodes:
            return None
        node = diagram_nodes[0]
        raw = node.get("image_data")
        fmt = str(node.get("image_format") or "").lower()
        req_w = int(node.get("requested_width") or width)
        req_h = int(node.get("requested_height") or height)
        target_w = min(req_w, self.max_width, width)
        target_h = min(req_h, self.max_height, height)
        if fmt in {"svg", "svg+xml"} and isinstance(raw, (bytes, bytearray)):
            png_bytes = None
            if _CAIROSVG_AVAILABLE:
                try:
                    png_bytes = cairosvg.svg2png(
                        bytestring=bytes(raw),
                        output_width=target_w,
                        output_height=target_h,
                    )
                except Exception:
                    pass
            if png_bytes:
                return png_bytes
        elif fmt == "png" and isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        start = time.perf_counter()
        w = max(1, min(int(width), self.max_width))
        h = max(1, min(int(height), self.max_height))

        metadata: dict[str, Any] = {
            "renderer": self.renderer_id,
            "scene_type": scene.scene_type,
            "animated": bool(scene.metadata.get("animated")),
        }

        # Prefer diagram_image nodes over scene.metadata svg_text/svg_path (MDP-011)
        diagram_nodes = [n for n in scene.nodes if isinstance(n, dict) and n.get("kind") == "diagram_image"]
        png_bytes: bytes | None = None

        if diagram_nodes:
            png_bytes = self._render_diagram_nodes(diagram_nodes, width=w, height=h)
            if png_bytes:
                metadata["diagram_node_count"] = len(diagram_nodes)
                metadata["source"] = "diagram_image_node"

        if png_bytes is None:
            # Fall back to scene.metadata svg_text/svg_path
            svg_text = self._load_svg_text(scene)
            png_bytes = self._svg_to_png(svg_text, width=w, height=h) if svg_text else None

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if png_bytes:
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

        reason = "cairosvg unavailable" if not _CAIROSVG_AVAILABLE else "no svg source"
        payload = self._placeholder_png(width=w, height=h, message=reason)
        metadata.update({"generation_ms": round(elapsed_ms, 4), "degraded": True, "degraded_reason": reason})
        return RenderFrame(
            frame_type="raster",
            width=w,
            height=h,
            payload=payload,
            mime_or_format="image/png",
            timestamp=context.now,
            metadata=metadata,
        )
