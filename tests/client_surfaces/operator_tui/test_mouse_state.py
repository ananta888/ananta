from __future__ import annotations

from client_surfaces.operator_tui.mouse import MouseState, normalize_mouse_state


def test_mouse_coordinates_are_clamped() -> None:
    state = normalize_mouse_state(
        None,
        x=999,
        y=-12,
        width=80,
        height=24,
        event_type="move",
        now=10.0,
    )
    assert state.x == 79
    assert state.y == 0


def test_mouse_move_click_and_scroll_are_normalized() -> None:
    state = normalize_mouse_state(
        MouseState(x=3, y=4, active=True, hover_started_at=2.0),
        x=3,
        y=4,
        width=100,
        height=30,
        event_type="down",
        buttons=1,
        scroll_delta=0,
        now=5.0,
    )
    assert state.last_event_type == "down"
    assert state.buttons == 1
    assert state.hover_started_at == 2.0

    scrolled = normalize_mouse_state(
        state,
        x=5,
        y=6,
        width=100,
        height=30,
        event_type="scroll_down",
        buttons=0,
        scroll_delta=1,
        now=6.0,
    )
    assert scrolled.last_event_type == "scroll_down"
    assert scrolled.scroll_delta == 1
    assert scrolled.x == 5 and scrolled.y == 6
