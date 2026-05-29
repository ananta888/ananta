from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None
    _PIL_AVAILABLE = False


def _clamp(value: int, maximum: int) -> int:
    return max(1, min(int(value), int(maximum)))


def _paste_png_node(image: "Any", node: dict[str, Any], *, max_w: int, max_h: int) -> None:
    """Paste a PNG diagram_image node into the output image."""
    raw = node.get("image_data")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        _draw_fallback_text(image, node)
        return
    try:
        diagram = Image.open(io.BytesIO(bytes(raw))).convert("RGBA")
        req_w = int(node.get("requested_width") or max_w)
        req_h = int(node.get("requested_height") or max_h)
        target_w = min(req_w, max_w)
        target_h = min(req_h, max_h)
        # Scale preserving aspect ratio
        orig_w, orig_h = diagram.size
        if orig_w > 0 and orig_h > 0:
            scale = min(target_w / orig_w, target_h / orig_h)
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))
            diagram = diagram.resize((new_w, new_h), Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.ANTIALIAS)  # type: ignore[attr-defined]
        px = int(node.get("x") or 0)
        py = int(node.get("y") or 0)
        image.paste(diagram, (px, py), diagram)
    except Exception:
        _draw_fallback_text(image, node)


def _try_svg_to_png(svg_bytes: bytes, *, width: int, height: int) -> bytes | None:
    """Convert SVG bytes to PNG using cairosvg if available."""
    try:
        import cairosvg  # type: ignore
        return cairosvg.svg2png(bytestring=svg_bytes, output_width=width, output_height=height)
    except Exception:
        return None


def _paste_svg_node(image: "Any", node: dict[str, Any], *, max_w: int, max_h: int) -> None:
    """Convert SVG diagram_image node to PNG and paste into the output image."""
    raw = node.get("image_data")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        _draw_fallback_text(image, node)
        return
    req_w = int(node.get("requested_width") or max_w)
    req_h = int(node.get("requested_height") or max_h)
    png_bytes = _try_svg_to_png(bytes(raw), width=min(req_w, max_w), height=min(req_h, max_h))
    if png_bytes:
        # Recurse with synthesized PNG node
        png_node = dict(node)
        png_node["image_format"] = "png"
        png_node["image_data"] = png_bytes
        _paste_png_node(image, png_node, max_w=max_w, max_h=max_h)
    else:
        _draw_fallback_text(image, node)


def _draw_fallback_text(image: "Any", node: dict[str, Any]) -> None:
    draw = ImageDraw.Draw(image)
    alt = str(node.get("alt_text") or node.get("fallback_text") or "[diagram]")
    x = int(node.get("x") or 0)
    y = int(node.get("y") or 0)
    draw.rectangle((x, y, x + 200, y + 30), fill=(40, 40, 60, 255))
    draw.text((x + 4, y + 8), alt[:40], fill=(200, 180, 120, 255))


@dataclass
class CpuRasterRenderer:
    renderer_id: str = "cpu_raster"
    max_width: int = 1280
    max_height: int = 720

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        start = time.perf_counter()
        w = _clamp(width, self.max_width)
        h = _clamp(height, self.max_height)

        if not _PIL_AVAILABLE:
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

        label_nodes: list[dict[str, Any]] = []
        diagram_nodes: list[dict[str, Any]] = []
        for node in scene.nodes:
            if not isinstance(node, dict):
                continue
            if node.get("kind") == "diagram_image":
                diagram_nodes.append(node)
            else:
                label_nodes.append(node)

        # Render text/label nodes
        draw.text((8, 8), f"{scene.scene_type}", fill=(240, 240, 240, 255))
        y_pos = 24
        for node in label_nodes[:30]:
            text = str(node.get("text") or node.get("kind") or "-")
            draw.text((8, y_pos), text[:120], fill=(200, 210, 220, 255))
            y_pos += 14
            if y_pos >= h - 12:
                break

        # Render diagram_image nodes (MIMG-007 / MDP-010)
        for node in diagram_nodes:
            fmt = str(node.get("image_format") or "").lower()
            if fmt == "png":
                _paste_png_node(image, node, max_w=w, max_h=h)
            elif fmt in {"svg", "svg+xml"}:
                _paste_svg_node(image, node, max_w=w, max_h=h)
            else:
                _draw_fallback_text(image, node)

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png = buf.getvalue()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        diagram_meta: dict[str, Any] = {}
        if diagram_nodes:
            diagram_meta = {
                "diagram_node_count": len(diagram_nodes),
                "diagram_ids": [str(n.get("diagram_id") or "") for n in diagram_nodes],
            }

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
                **diagram_meta,
            },
        )
