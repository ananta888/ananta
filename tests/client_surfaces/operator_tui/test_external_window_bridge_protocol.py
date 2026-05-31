from __future__ import annotations

from client_surfaces.operator_tui.windowing.protocol import allowed_actions, is_allowed_action


def test_window_action_allowlist_contains_expected_actions() -> None:
    actions = set(allowed_actions())
    assert "snake.pause" in actions
    assert "snake.resume" in actions
    assert "view.next" in actions
    assert "view.previous" in actions


def test_window_action_rejects_unknown_action() -> None:
    assert is_allowed_action("view.next") is True
    assert is_allowed_action("totally.unknown.action") is False
