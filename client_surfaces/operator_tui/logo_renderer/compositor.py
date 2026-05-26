from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency path
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]


def compose_overlay(base: PixelFrame, overlay: PixelFrame, *, x: int = 0, y: int = 0) -> PixelFrame:
    if base.is_empty:
        return overlay
    if overlay.is_empty or Image is None:
        return base
    base_img = Image.frombytes("RGBA", (base.width_px, base.height_px), base.rgba)
    ov_img = Image.frombytes("RGBA", (overlay.width_px, overlay.height_px), overlay.rgba)
    base_img.alpha_composite(ov_img, dest=(max(0, int(x)), max(0, int(y))))
    meta = dict(base.metadata)
    meta.update({"composited": True, "overlay_renderer": str(overlay.metadata.get("renderer", ""))})
    return PixelFrame.from_image(base_img, metadata=meta)


def compose_text_overlay(
    frame: PixelFrame,
    *,
    lines: list[str],
    x: int = 8,
    y: int = 8,
    color: tuple[int, int, int, int] = (230, 240, 250, 240),
) -> PixelFrame:
    if frame.is_empty or Image is None or ImageDraw is None:
        return frame
    img = Image.frombytes("RGBA", (frame.width_px, frame.height_px), frame.rgba)
    draw = ImageDraw.Draw(img, "RGBA")
    line_h = 16
    for idx, line in enumerate(lines):
        draw.text((x, y + idx * line_h), line, fill=color)
    meta = dict(frame.metadata)
    meta.update({"overlay_text": True})
    return PixelFrame.from_image(img, metadata=meta)
