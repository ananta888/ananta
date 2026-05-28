from __future__ import annotations

from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion
from client_surfaces.operator_tui.visual.views.artifact_preview_view import ArtifactPreviewView
from client_surfaces.operator_tui.visual.views.base_view import ViewContext
from client_surfaces.operator_tui.visual.views.logo_animation_view import LogoAnimationView
from client_surfaces.operator_tui.visual.views.snake_debug_view import SnakeDebugView


def _context(state: dict[str, object] | None = None) -> ViewContext:
    region = ViewportRegion(x=0, y=0, columns=80, rows=24, pixel_width=800, pixel_height=450)
    return ViewContext(region=region, now=1.0, state=dict(state or {}))


def test_logo_animation_view_is_deterministic_when_paused() -> None:
    view = LogoAnimationView()
    view.update(0.2, {"paused": True})
    scene = view.render(_context())
    assert scene.scene_type == "logo_animation"
    assert any(node.get("kind") == "title" for node in scene.nodes)


def test_snake_debug_view_emits_snake_target_and_metadata() -> None:
    view = SnakeDebugView()
    scene = view.render(
        _context(
            {
                "snake": [(4, 3), (3, 3), (2, 3)],
                "target": (10, 6),
                "selected_heuristic": "follow_mouse",
                "heuristic_confidence": 0.77,
            }
        )
    )
    assert scene.scene_type == "snake_debug"
    assert any(node.get("kind") == "snake_head" for node in scene.nodes)
    assert any(node.get("kind") == "target" for node in scene.nodes)


def test_artifact_preview_rejects_path_outside_allowed_roots() -> None:
    view = ArtifactPreviewView()
    scene = view.render(
        _context(
            {
                "artifact": {"path": "/etc/passwd", "title": "passwd", "kind": "text"},
                "allowed_roots": ["/home/krusty/ananta"],
            }
        )
    )
    assert scene.scene_type == "artifact_preview"
    assert any(node.get("kind") == "error" for node in scene.nodes)
