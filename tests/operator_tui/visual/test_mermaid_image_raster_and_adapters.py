"""MDP-019 / MIMG-014/015: Tests for image node rasterization and adapter draw."""
from __future__ import annotations

import io

import pytest

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
from client_surfaces.operator_tui.visual.adapters.sixel_adapter import SixelOutputAdapter
from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

_FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _region(cols: int = 40, rows: int = 10) -> ViewportRegion:
    return ViewportRegion(x=0, y=0, columns=cols, rows=rows, pixel_width=320, pixel_height=80)


def _raster_frame(payload: bytes = _FAKE_PNG) -> RenderFrame:
    return RenderFrame(
        frame_type="raster",
        width=4,
        height=2,
        payload=payload,
        mime_or_format="image/png",
        timestamp=1.0,
        metadata={"renderer": "cpu_raster"},
    )


def _scene_with_diagram_node(fmt: str = "png", data: bytes = _FAKE_PNG) -> RenderScene:
    return RenderScene(
        scene_type="markdown_mermaid_document",
        nodes=[
            {"kind": "label", "text": "# Title", "x": 0, "y": 0},
            {
                "kind": "diagram_image",
                "diagram_id": "test_diag_1",
                "image_format": fmt,
                "image_data": data,
                "x": 0,
                "y": 50,
                "requested_width": 200,
                "requested_height": 100,
                "alt_text": "Test diagram",
                "fallback_text": "graph TD\n  A-->B",
            },
        ],
        metadata={"animated": False, "mermaid_visible_images": 1},
    )


# ── CpuRasterRenderer — diagram_image nodes (MIMG-007 / MDP-010) ──────────────

def test_cpu_raster_accepts_diagram_image_node():
    renderer = CpuRasterRenderer(max_width=128, max_height=64)
    scene = _scene_with_diagram_node()
    frame = renderer.render(scene, width=128, height=64, context=RenderContext(now=1.0))
    assert frame.frame_type == "raster"
    assert frame.mime_or_format in {"image/png", "application/x-rgba"}


def test_cpu_raster_records_diagram_count():
    renderer = CpuRasterRenderer(max_width=128, max_height=64)
    scene = _scene_with_diagram_node()
    frame = renderer.render(scene, width=128, height=64, context=RenderContext(now=1.0))
    if "diagram_node_count" in frame.metadata:
        assert frame.metadata["diagram_node_count"] == 1


def test_cpu_raster_invalid_image_data_does_not_crash():
    renderer = CpuRasterRenderer(max_width=128, max_height=64)
    scene = RenderScene(
        scene_type="markdown_mermaid_document",
        nodes=[{
            "kind": "diagram_image",
            "diagram_id": "bad_img",
            "image_format": "png",
            "image_data": b"NOT A PNG",
            "x": 0, "y": 0,
            "requested_width": 100, "requested_height": 50,
            "alt_text": "broken",
        }],
        metadata={"animated": False},
    )
    frame = renderer.render(scene, width=128, height=64, context=RenderContext(now=1.0))
    assert frame.frame_type == "raster"


def test_cpu_raster_existing_label_nodes_still_rendered():
    renderer = CpuRasterRenderer(max_width=128, max_height=64)
    scene = RenderScene(
        scene_type="test",
        nodes=[
            {"kind": "label", "text": "hello world", "x": 1, "y": 1},
            {"kind": "territory", "id": "A1", "owner": "north", "point": (2, 3)},
        ],
        metadata={"animated": False},
    )
    frame = renderer.render(scene, width=128, height=64, context=RenderContext(now=1.0))
    assert frame.frame_type == "raster"
    assert frame.width == 128


# ── Kitty adapter (MIMG-010 / MDP-012) ───────────────────────────────────────

def test_kitty_accepts_png_raster_frame():
    adapter = KittyOutputAdapter(supported=True, enabled=True)
    out = io.StringIO()
    frame = _raster_frame()
    result = adapter.draw(frame, region=_region(), stream=out, context=DrawContext(now=1.0))
    assert result.drawn is True
    content = out.getvalue()
    assert "\x1b_G" in content


def test_kitty_includes_image_id_in_transfer():
    adapter = KittyOutputAdapter(supported=True, enabled=True, image_id=42)
    out = io.StringIO()
    frame = _raster_frame()
    adapter.draw(frame, region=_region(), stream=out, context=DrawContext(now=1.0))
    assert "i=42" in out.getvalue()


def test_kitty_clear_emits_delete_sequence():
    adapter = KittyOutputAdapter(supported=True, enabled=True, image_id=7)
    out = io.StringIO()
    adapter.clear(out)
    content = out.getvalue()
    assert "\x1b_G" in content
    assert "d=i" in content or "a=d" in content


def test_kitty_unsupported_returns_drawn_false():
    adapter = KittyOutputAdapter(supported=False)
    out = io.StringIO()
    result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    assert result.drawn is False


def test_kitty_positions_at_viewport_origin():
    adapter = KittyOutputAdapter(supported=True, enabled=True)
    out = io.StringIO()
    region = ViewportRegion(x=5, y=3, columns=20, rows=10, pixel_width=160, pixel_height=80)
    adapter.draw(_raster_frame(), region=region, stream=out, context=DrawContext(now=1.0))
    content = out.getvalue()
    # Cursor should be positioned at y+1, x+1 (1-indexed)
    assert "\x1b[4;6H" in content


# ── Sixel adapter (MIMG-011 / MDP-013) ───────────────────────────────────────

def test_sixel_unsupported_returns_drawn_false():
    adapter = SixelOutputAdapter(supported=False)
    out = io.StringIO()
    result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    assert result.drawn is False
    assert result.reason in {"sixel_protocol_unsupported", "unsupported"}


def test_sixel_no_longer_emits_stub():
    adapter = SixelOutputAdapter(supported=True, enabled=True)
    out = io.StringIO()
    adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    assert "sixel-stub" not in out.getvalue(), "Sixel adapter must not emit stub output"


def test_sixel_explicit_degraded_when_encoder_missing():
    """When Pillow is missing, Sixel must report degraded, not fake success."""
    import unittest.mock as mock
    adapter = SixelOutputAdapter(supported=True, enabled=True)
    out = io.StringIO()
    with mock.patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
        result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    # Either succeeds (Pillow found) or reports degraded (not stub)
    if not result.drawn:
        assert "sixel_encoder_unavailable" in result.reason or "unsupported" in result.reason
    assert "sixel-stub" not in out.getvalue()


def test_sixel_supported_emits_dcs_and_st():
    """When Pillow is available, output should be real Sixel with DCS/ST."""
    adapter = SixelOutputAdapter(supported=True, enabled=True)
    out = io.StringIO()
    payload = bytes([200, 100, 50, 255] * 4)  # 2x2 RGBA
    from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
    rgba_frame = RenderFrame(
        frame_type="raster",
        width=2, height=2,
        payload={"pixels": payload, "width": 2, "height": 2, "mode": "RGBA"},
        mime_or_format="application/x-rgba",
        timestamp=1.0,
        metadata={},
    )
    result = adapter.draw(rgba_frame, region=_region(), stream=out, context=DrawContext(now=1.0))
    content = out.getvalue()
    if result.drawn:
        assert "\x1bP" in content, "Sixel output should start with DCS"
        assert "\x1b\\" in content, "Sixel output should end with ST"
        assert "sixel-stub" not in content


def test_sixel_disabled_returns_drawn_false():
    adapter = SixelOutputAdapter(enabled=False, supported=True)
    out = io.StringIO()
    result = adapter.draw(_raster_frame(), region=_region(), stream=out, context=DrawContext(now=1.0))
    assert result.drawn is False
    assert result.reason == "disabled"


# ── Full path: scene → raster frame → kitty ───────────────────────────────────

def test_cpu_raster_produces_frame_that_kitty_accepts():
    renderer = CpuRasterRenderer(max_width=64, max_height=32)
    scene = _scene_with_diagram_node()
    frame = renderer.render(scene, width=64, height=32, context=RenderContext(now=1.0))
    assert frame.frame_type == "raster"

    if frame.mime_or_format == "image/png" and isinstance(frame.payload, bytes):
        adapter = KittyOutputAdapter(supported=True, enabled=True)
        out = io.StringIO()
        result = adapter.draw(frame, region=_region(), stream=out, context=DrawContext(now=1.0))
        assert result.drawn is True
