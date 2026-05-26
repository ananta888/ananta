from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.detect import (
    detect_kitty_support,
    detect_sixel_support,
    detect_terminal_graphics_capabilities,
    select_graphics_backend,
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


def test_select_graphics_backend_auto_for_wezterm_prefers_kitty():
    env = {
        "TERM_PROGRAM": "WezTerm",
        "WEZTERM_EXECUTABLE": "/usr/bin/wezterm",
        "COLORTERM": "truecolor",
    }
    caps = detect_terminal_graphics_capabilities(env)
    assert caps.kitty is True
    assert select_graphics_backend(env=env, capabilities=caps) == "kitty"


def test_select_graphics_backend_auto_for_windows_terminal_prefers_sixel():
    env = {
        "TERM": "xterm-256color",
        "WT_SESSION": "session",
    }
    caps = detect_terminal_graphics_capabilities(env)
    assert caps.sixel is True
    assert select_graphics_backend(env=env, capabilities=caps) == "sixel"


def test_select_graphics_backend_unknown_terminal_falls_back_safely():
    env = {
        "TERM": "dumb",
        "COLORTERM": "",
        "NO_COLOR": "1",
    }
    caps = detect_terminal_graphics_capabilities(env)
    assert select_graphics_backend(env=env, capabilities=caps) == "ascii"


def test_select_graphics_backend_respects_forced_override():
    assert select_graphics_backend(env={"ANANTA_TUI_GRAPHICS": "halfblock"}) == "halfblock"
