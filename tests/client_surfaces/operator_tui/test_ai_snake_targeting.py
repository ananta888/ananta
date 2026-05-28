from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_follow import make_follow_state, step_follow_state
from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import FocusPane, OperatorState


def _base_game() -> dict[str, object]:
    return {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(10, 8), (9, 8), (8, 8)],
        "trail_path": [(10, 8), (9, 8), (8, 8)],
        "mark_cells": [],
        "selection_cells": [],
        "selection_regions": [],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "vel_x": 0.0,
        "vel_y": 0.0,
        "accum_x": 0.0,
        "accum_y": 0.0,
        "last_move": 0.0,
        "artifact_intent_confidence": "none",
    }


def test_ai_snake_follows_user_by_default() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=_base_game())
    tui = InteractiveOperatorTui(state)
    game = dict(tui.state.header_logo_game or {})
    snakes: dict[str, dict[str, object]] = {}
    tui._update_tutorial_ai_snake(game, snakes, now=1.0, board_w=120, board_h=32, enabled=True)
    assert "s-ai" in snakes
    assert snakes["s-ai"].get("mode") in {"follow_user", "follow"}


def test_ai_snake_fast_targets_confirmed_artifact() -> None:
    game = _base_game()
    game["artifact_intent_confidence"] = "confirmed"
    game["artifact_target_cell"] = (90, 20)
    game["artifact_intent_target"] = {"label": "Artifacts", "pane": "content", "payload": {}}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    snakes: dict[str, dict[str, object]] = {}
    tui._update_tutorial_ai_snake(game, snakes, now=1.2, board_w=120, board_h=32, enabled=True)
    assert snakes["s-ai"].get("mode") in {"fast_target", "explain_target"}


def test_ai_snake_enters_explain_mode_on_arrival() -> None:
    game = _base_game()
    game["artifact_intent_confidence"] = "confirmed"
    game["artifact_target_cell"] = (11, 8)
    game["artifact_intent_target"] = {"label": "Artifacts", "pane": "content", "payload": {}}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    snakes: dict[str, dict[str, object]] = {}
    tui._update_tutorial_ai_snake(game, snakes, now=2.0, board_w=120, board_h=32, enabled=True)
    assert game.get("tutorial_ai_target_mode") in {"fast_target", "explain_target"}


def test_ai_snake_steps_across_wrapped_edge_without_wall() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=_base_game())
    tui = InteractiveOperatorTui(state)

    next_pos = tui._step_toward_cell(current=(118, 8), target=(2, 8), board_w=120, board_h=32)

    assert next_pos == (119, 8)


def test_ai_follow_state_uses_wrapped_shortest_path() -> None:
    follow = make_follow_state(ai_position=(118, 8), mode="follow", follow_distance=1, linger_distance=2)

    updated = step_follow_state(follow, user_position=(2, 8), board_w=120, board_h=32)

    assert updated["ai_position"] == (119, 8)
