from __future__ import annotations

from client_surfaces.operator_tui.mouse import detect_mouse_support


def test_mouse_support_flag_enabled() -> None:
    decision = detect_mouse_support({"TERM": "xterm-256color", "ANANTA_TUI_MOUSE": "1"})
    assert decision["enabled"] is True
    assert decision["reason"] == "enabled-by-env"


def test_mouse_support_fallback_without_terminal_mouse() -> None:
    decision = detect_mouse_support({"TERM": "dumb", "ANANTA_TUI_MOUSE": "0"})
    assert decision["enabled"] is False
    assert decision["reason"] == "disabled-by-env"
