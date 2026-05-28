from __future__ import annotations

from client_surfaces.operator_tui.visual.renderers.ansi_renderer import AnsiBlocksRenderer
from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
from client_surfaces.operator_tui.visual.renderers.svg_raster_renderer import SvgRasterRenderer
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene


def _scene(name: str) -> RenderScene:
    return RenderScene(
        scene_type=name,
        nodes=[
            {"kind": "label", "text": "hello", "x": 1, "y": 1},
            {"kind": "territory", "id": "A1", "owner": "north", "point": (2, 3)},
        ],
        metadata={"animated": False},
    )


def test_ansi_renderer_clips_and_returns_deterministic_text_frame() -> None:
    renderer = AnsiBlocksRenderer()
    frame = renderer.render(_scene("diagnostics"), width=20, height=6, context=RenderContext(now=1.0))
    assert frame.frame_type == "ansi"
    assert frame.width == 20
    assert frame.height == 6
    assert isinstance(frame.payload, list)
    assert len(frame.payload) == 6
    assert "[diagnostics]" in frame.payload[0]


def test_cpu_raster_renderer_returns_raster_payload_with_clamping() -> None:
    renderer = CpuRasterRenderer(max_width=128, max_height=64)
    frame = renderer.render(_scene("logo_animation"), width=999, height=777, context=RenderContext(now=2.0))
    assert frame.frame_type == "raster"
    assert frame.width == 128
    assert frame.height == 64
    assert frame.mime_or_format in {"image/png", "application/x-rgba"}
    assert frame.metadata.get("renderer") == "cpu_raster"


def test_svg_raster_renderer_falls_back_without_svg_dependency_or_input() -> None:
    renderer = SvgRasterRenderer(max_width=64, max_height=32)
    scene = RenderScene(scene_type="logo_animation", nodes=[], metadata={})
    frame = renderer.render(scene, width=64, height=32, context=RenderContext(now=3.0))
    assert frame.frame_type == "raster"
    assert frame.width == 64
    assert frame.height == 32
    assert frame.metadata.get("renderer") == "svg_raster_optional"
    assert frame.metadata.get("degraded") is True

