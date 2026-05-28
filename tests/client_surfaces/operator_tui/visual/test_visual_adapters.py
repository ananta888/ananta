from __future__ import annotations

from io import StringIO

from client_surfaces.operator_tui.visual.adapters.ansi_adapter import AnsiOutputAdapter
from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
from client_surfaces.operator_tui.visual.adapters.noop_adapter import NoopDiagnosticsAdapter
from client_surfaces.operator_tui.visual.adapters.sixel_adapter import SixelOutputAdapter
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


def _region() -> ViewportRegion:
    return ViewportRegion(x=10, y=5, columns=8, rows=3, pixel_width=80, pixel_height=24)


def _ansi_frame() -> RenderFrame:
    return RenderFrame(
        frame_type="ansi",
        width=8,
        height=3,
        payload=["abcdefghijk", "x", ""],
        mime_or_format="text/plain",
        timestamp=1.0,
        metadata={"renderer": "ansi_blocks"},
    )


def _raster_frame() -> RenderFrame:
    return RenderFrame(
        frame_type="raster",
        width=4,
        height=2,
        payload=b"\x89PNGdemo",
        mime_or_format="image/png",
        timestamp=1.0,
        metadata={"renderer": "cpu_raster"},
    )


def test_ansi_output_adapter_clips_and_positions_lines() -> None:
    adapter = AnsiOutputAdapter()
    out = StringIO()
    result = adapter.draw(_ansi_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    content = out.getvalue()
    assert result.drawn is True
    assert "\x1b[6;11Habcdefgh" in content
    assert "\x1b[7;11Hx       " in content


def test_sixel_adapter_reports_unsupported_and_can_draw_when_enabled() -> None:
    adapter = SixelOutputAdapter(enabled=True, supported=False)
    out = StringIO()
    result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    assert result.drawn is False
    assert result.reason == "unsupported"

    adapter.supported = True
    result2 = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.1))
    assert result2.drawn is True
    assert "sixel-stub" in out.getvalue()


def test_kitty_adapter_clear_and_draw_behaviour() -> None:
    adapter = KittyOutputAdapter(enabled=True, supported=True, image_id=42)
    out = StringIO()
    adapter.clear(out)
    clear_out = out.getvalue()
    assert "a=d" in clear_out
    assert "i=42" in clear_out

    result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=2.0))
    assert result.drawn is True
    assert "\x1b_Ga=T" in out.getvalue()


def test_noop_adapter_records_draw_calls_without_writing() -> None:
    adapter = NoopDiagnosticsAdapter()
    out = StringIO()
    result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=3.0))
    assert result.drawn is False
    assert result.reason == "noop"
    assert adapter.draw_count == 1
    assert out.getvalue() == ""

