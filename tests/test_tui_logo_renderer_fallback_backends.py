from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.ascii import AsciiRenderer
from client_surfaces.operator_tui.logo_renderer.halfblock import HalfblockRenderer
from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame


def test_ascii_renderer_reports_fallback_capabilities() -> None:
    renderer = AsciiRenderer()
    caps = renderer.get_capabilities()
    assert caps["renderer"] == "ascii"
    assert caps["ascii_fallback"] is True
    assert renderer.supports_truecolor() is False


def test_halfblock_renderer_reports_truecolor_fallback() -> None:
    renderer = HalfblockRenderer()
    caps = renderer.get_capabilities()
    assert caps["renderer"] == "halfblock"
    assert caps["fallback_renderer"] is True
    assert renderer.supports_truecolor() is True


def test_ascii_frame_render_path_with_stubbed_svg(monkeypatch) -> None:
    renderer = AsciiRenderer()
    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.ascii.frame_from_svg",
        lambda **kwargs: PixelFrame(width_px=4, height_px=4, rgba=b"\x7f" * (4 * 4 * 4)),
    )
    frame = renderer.render_frame(width_cells=4, height_cells=2)
    assert frame.kind == "text_lines"
    assert len(frame.text_lines) == 2
