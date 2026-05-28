from __future__ import annotations

from client_surfaces.operator_tui.visual.renderers.ansi_renderer import AnsiBlocksRenderer
from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.views.base_view import ViewContext
from client_surfaces.operator_tui.visual.views.logo_animation_view import LogoAnimationView
from client_surfaces.operator_tui.visual.views.renderer_diagnostics_view import RendererDiagnosticsView
from client_surfaces.operator_tui.visual.views.snake_debug_view import SnakeDebugView
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


def _region() -> ViewportRegion:
    return ViewportRegion(x=0, y=0, columns=24, rows=8, pixel_width=240, pixel_height=80)


def _render_scene_snapshot(view, *, state: dict[str, object], width: int = 24, height: int = 8) -> list[str]:
    scene = view.render(ViewContext(region=_region(), now=1.0, state=dict(state)))
    frame = AnsiBlocksRenderer().render(scene, width=width, height=height, context=RenderContext(now=1.0))
    assert frame.frame_type == "ansi"
    assert isinstance(frame.payload, list)
    return [str(row) for row in frame.payload]


def test_ansi_snapshot_logo_animation_view() -> None:
    view = LogoAnimationView()
    view.update(0.0, {"paused": True})

    payload = _render_scene_snapshot(view, state={"paused": True})

    assert payload[:3] == [
        "ANANTA |                ",
        "                        ",
        "                        ",
    ]
    assert all("\x1b_G" not in line and "\x1bPq" not in line for line in payload)


def test_ansi_snapshot_snake_debug_view() -> None:
    view = SnakeDebugView()
    payload = _render_scene_snapshot(
        view,
        state={
            "snake": [(4, 3), (3, 3), (2, 3)],
            "target": (8, 3),
            "selected_heuristic": "follow_mouse",
            "heuristic_confidence": 0.77,
        },
    )

    assert payload[:4] == ["heuristic=follow_mouse  ", "confidence=0.77         ", "                        ", "◎ target                "]
    assert all("\x1b_G" not in line and "\x1bPq" not in line for line in payload)


def test_ansi_snapshot_renderer_diagnostics_view() -> None:
    view = RendererDiagnosticsView()
    payload = _render_scene_snapshot(
        view,
        state={
            "runtime_status": {
                "active_view": "renderer_diagnostics",
                "active_renderer": "ansi_blocks",
                "active_adapter": "ansi",
                "fps": "10",
                "fallback_reason": "-",
            }
        },
    )

    assert payload[:4] == [
        "view=renderer_diagnostic",
        "renderer=ansi_blocks    ",
        "adapter=ansi            ",
        "fps=10                  ",
    ]
    assert all("\x1b_G" not in line and "\x1bPq" not in line for line in payload)
