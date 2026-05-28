from __future__ import annotations

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.renderers.opengl_offscreen_renderer import OpenGlOffscreenRenderer
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene


def test_opengl_renderer_reports_unavailable_without_dependency(monkeypatch) -> None:
    renderer = OpenGlOffscreenRenderer()
    monkeypatch.setattr(renderer, "_check_available", lambda: (False, "opengl unavailable"))
    scene = RenderScene(scene_type="demo", nodes=[], metadata={})
    try:
        renderer.render(scene, width=64, height=32, context=RenderContext(now=1.0))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "opengl unavailable" in str(exc)
