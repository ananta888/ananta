from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.detect import (
    detect_kitty_support,
    detect_sixel_support,
    resolve_renderer,
)


def test_detect_sixel_support_from_term():
    assert detect_sixel_support({"TERM": "xterm-sixel"}) is True


def test_detect_kitty_support_from_env():
    assert detect_kitty_support({"KITTY_WINDOW_ID": "11"}) is True


def test_resolve_renderer_auto_prefers_kitty_then_sixel_then_ansi():
    assert resolve_renderer(env={"ANANTA_TUI_LOGO_RENDERER": "auto"}, sixel_available=True, kitty_available=True).selected == "kitty"
    assert resolve_renderer(env={"ANANTA_TUI_LOGO_RENDERER": "auto"}, sixel_available=True, kitty_available=False).selected == "sixel"
    assert resolve_renderer(env={"ANANTA_TUI_LOGO_RENDERER": "auto"}, sixel_available=False, kitty_available=False).selected == "ansi"


def test_resolve_renderer_manual_override_and_fallback_warning():
    decision = resolve_renderer(env={"ANANTA_TUI_LOGO_RENDERER": "kitty"}, sixel_available=False, kitty_available=False)
    assert decision.selected == "ansi"
    assert "unavailable" in decision.warning.lower()


def test_resolve_renderer_unknown_value_does_not_crash():
    decision = resolve_renderer(env={"ANANTA_TUI_LOGO_RENDERER": "nonsense"}, sixel_available=False, kitty_available=False)
    assert decision.selected == "ansi"


def test_resolve_renderer_respects_logo_disable():
    decision = resolve_renderer(env={"ANANTA_TUI_LOGO": "0"}, sixel_available=True, kitty_available=True)
    assert decision.selected == "none"
