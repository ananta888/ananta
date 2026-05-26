from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.moderngl_renderer import ModernGLOffscreenRenderer
from client_surfaces.operator_tui.logo_renderer.raylib_renderer import RaylibPrototypeRenderer
from client_surfaces.operator_tui.logo_renderer.renderer_3d import SceneConfig


def test_moderngl_renderer_returns_offscreen_frame() -> None:
    renderer = ModernGLOffscreenRenderer()
    frame = renderer.render_scene(config=SceneConfig(width_px=240, height_px=140, t=0.2))
    assert frame.width_px == 240
    assert frame.height_px == 140
    assert frame.is_empty is False
    assert frame.metadata.get("renderer") == "moderngl"


def test_raylib_renderer_returns_offscreen_frame() -> None:
    renderer = RaylibPrototypeRenderer()
    frame = renderer.render_scene(config=SceneConfig(width_px=220, height_px=120, t=0.4))
    assert frame.width_px == 220
    assert frame.height_px == 120
    assert frame.is_empty is False
    assert frame.metadata.get("renderer") == "raylib"
