from __future__ import annotations

from types import SimpleNamespace

from client_surfaces.operator_tui.windowing.view_models.ai_snake_window_model import build_ai_snake_window_model
from client_surfaces.operator_tui.windowing.view_models.center_window_model import build_center_window_model


def test_ai_snake_window_model_is_serializable_shape() -> None:
    game = {
        "snake_mode": True,
        "paused": False,
        "tutorial_mode": True,
        "ai_snake_runtime_status": "running",
        "ai_snake_mode": "lurking_follow",
        "selected_heuristic_id": "h1",
        "heuristic_confidence": 0.72,
    }
    model = build_ai_snake_window_model(game)
    assert model["active"] is True
    assert model["runtime_status"] == "running"
    assert model["heuristic_id"] == "h1"


def test_center_window_model_maps_state_and_game() -> None:
    state = SimpleNamespace(section_id="dashboard", focus=SimpleNamespace(value="content"), status_message="ok")
    game = {"center_window_view_mode": "doc", "visual_viewport_active_view": "markdown_mermaid_document", "center_browser_active": False}
    model = build_center_window_model(state=state, game=game)
    assert model["mode"] == "doc"
    assert model["section"] == "dashboard"
    assert model["focus"] == "content"
