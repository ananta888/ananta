from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_follow import apply_worker_follow_update, make_follow_state, step_follow_state


def test_follow_state_switches_lurk_to_follow_and_moves() -> None:
    state = make_follow_state(ai_position=(0, 0), mode="lurking_follow", follow_distance=2, linger_distance=4)
    state = step_follow_state(state, user_position=(7, 0), board_w=20, board_h=8)
    assert state["mode"] == "follow"
    assert state["ai_position"] != (0, 0)


def test_follow_state_lingers_when_close() -> None:
    state = make_follow_state(ai_position=(3, 3), mode="follow", follow_distance=3, linger_distance=7)
    state = step_follow_state(state, user_position=(4, 3), board_w=20, board_h=8)
    assert state["mode"] == "lurking"


def test_worker_follow_update_applies_only_allowed_modes() -> None:
    state = make_follow_state(mode="follow")
    changed = apply_worker_follow_update(state, follow_mode_update="point_to_target", prediction_target="section:tasks", confidence=0.8)
    assert changed["mode"] == "point_to_target"
    assert changed["prediction_target"] == "section:tasks"
    unchanged = apply_worker_follow_update(changed, follow_mode_update="dangerous_custom_mode", prediction_target="", confidence=0.1)
    assert unchanged["mode"] == "point_to_target"
