from __future__ import annotations

from PIL import Image

from client_surfaces.operator_tui.logo_renderer.compositor import compose_overlay, compose_text_overlay
from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame


def _frame(width: int, height: int, rgba: tuple[int, int, int, int]) -> PixelFrame:
    return PixelFrame.from_image(Image.new("RGBA", (width, height), rgba), metadata={"renderer": "test"})


def test_compose_overlay_combines_frames() -> None:
    base = _frame(40, 20, (10, 20, 30, 255))
    overlay = _frame(10, 6, (240, 40, 80, 220))
    merged = compose_overlay(base, overlay, x=5, y=4)
    assert merged.width_px == 40
    assert merged.height_px == 20
    assert merged.metadata.get("composited") is True


def test_compose_text_overlay_marks_metadata() -> None:
    base = _frame(50, 30, (0, 0, 0, 255))
    out = compose_text_overlay(base, lines=["renderer=demo", "fps=10"])
    assert out.width_px == 50
    assert out.height_px == 30
    assert out.metadata.get("overlay_text") is True
