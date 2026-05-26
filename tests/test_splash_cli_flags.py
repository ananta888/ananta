from __future__ import annotations

import os
from unittest.mock import patch

from agent.cli.splash import SplashMachine, SplashState
from agent.cli.logo_assets import load_logo, clear_asset_cache


def test_splash_disabled_by_env_var(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_SPLASH", "0")
    sm = SplashMachine(clock=lambda: 0.0)
    assert sm.context.state == SplashState.DISABLED


def test_splash_disabled_no_logo(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO", "0")
    clear_asset_cache()
    logo = load_logo(width=90, color=False)
    assert logo == ""


def test_splash_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ANANTA_TUI_SPLASH", raising=False)
    sm = SplashMachine(clock=lambda: 0.0)
    assert sm.context.state == SplashState.FULLSCREEN


def test_splash_skip_flag():
    sm = SplashMachine(clock=lambda: 0.0)
    sm.skip()
    assert sm.context.state == SplashState.SKIPPED


def test_splash_no_color_respected(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    clear_asset_cache()
    logo = load_logo(width=90, color=None)
    assert logo is not None
    assert "\x1b[" not in logo


def test_parse_args_skip_splash():
    from client_surfaces.operator_tui.app import _parse_args
    args = _parse_args(["--skip-splash"])
    assert args.skip_splash is True


def test_parse_args_splash_seconds():
    from client_surfaces.operator_tui.app import _parse_args
    args = _parse_args(["--splash-seconds", "3.5"])
    assert args.splash_seconds == 3.5


def test_parse_args_default_splash_seconds():
    from client_surfaces.operator_tui.app import _parse_args
    args = _parse_args([])
    assert args.splash_seconds == 2.0


def test_parse_args_skip_splash_default():
    from client_surfaces.operator_tui.app import _parse_args
    args = _parse_args([])
    assert args.skip_splash is False


def test_parse_args_logo_renderer_animation_flags():
    from client_surfaces.operator_tui.app import _parse_args
    args = _parse_args(["--logo-renderer", "ansi", "--logo-animation", "shimmer", "--logo-fps", "7", "--no-logo"])
    assert args.logo_renderer == "ansi"
    assert args.logo_animation == "shimmer"
    assert args.logo_fps == 7
    assert args.no_logo is True


def test_apply_logo_runtime_overrides_sets_env_for_render_once(monkeypatch):
    from client_surfaces.operator_tui.app import _apply_logo_runtime_overrides, _parse_args

    monkeypatch.delenv("ANANTA_TUI_LOGO_ANIMATION", raising=False)
    args = _parse_args(["--render-once", "--logo-renderer", "ansi"])
    _apply_logo_runtime_overrides(args)
    assert os.environ.get("ANANTA_TUI_LOGO_RENDERER") == "ansi"
    assert os.environ.get("ANANTA_TUI_LOGO_ANIMATION") == "static"


def test_splash_debug_file_not_written_without_flag(tmp_path, monkeypatch):
    from client_surfaces.operator_tui.app import _maybe_write_splash_debug

    debug_path = tmp_path / "splash_debug.txt"
    monkeypatch.delenv("ANANTA_TUI_SPLASH_DEBUG", raising=False)
    monkeypatch.setenv("ANANTA_TUI_SPLASH_DEBUG_PATH", str(debug_path))
    _maybe_write_splash_debug({"mode": "test"})
    assert debug_path.exists() is False


def test_splash_debug_file_written_with_flag(tmp_path, monkeypatch):
    from client_surfaces.operator_tui.app import _maybe_write_splash_debug

    debug_path = tmp_path / "splash_debug.txt"
    monkeypatch.setenv("ANANTA_TUI_SPLASH_DEBUG", "1")
    monkeypatch.setenv("ANANTA_TUI_SPLASH_DEBUG_PATH", str(debug_path))
    _maybe_write_splash_debug({"mode": "test", "frames": 3})
    assert debug_path.exists() is True
    body = debug_path.read_text(encoding="utf-8")
    assert "mode=test" in body
    assert "frames=3" in body


def test_render_once_without_splash():
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode, FocusPane
    from client_surfaces.operator_tui.renderer import render_operator_shell

    state = OperatorState(
        endpoint="http://localhost:5000",
        auth_state="test",
        mode=OperatorMode.NORMAL,
        focus=FocusPane.CONTENT,
        section_id="dashboard",
        terminal_graphics={"no_color": True},
    )
    output = render_operator_shell(state, width=120, height=32, splash=None)
    assert "Ananta Operator TUI" in output
    assert len(output) > 0


def test_render_once_with_compact_splash():
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode, FocusPane
    from client_surfaces.operator_tui.renderer import render_operator_shell

    sm = SplashMachine(fullscreen_seconds=0.0, transition_seconds=0.001, clock=lambda: 0.0)
    sm.tick(now=0.0)
    sm.tick(now=1.0)

    state = OperatorState(
        endpoint="http://localhost:5000",
        auth_state="test",
        mode=OperatorMode.NORMAL,
        focus=FocusPane.CONTENT,
        section_id="dashboard",
        terminal_graphics={"no_color": True},
    )
    output = render_operator_shell(state, width=120, height=32, splash=sm)
    assert len(output) > 0


def test_render_once_with_skip_splash():
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode, FocusPane
    from client_surfaces.operator_tui.renderer import render_operator_shell

    sm = SplashMachine(clock=lambda: 0.0)
    sm.disable()

    state = OperatorState(
        endpoint="http://localhost:5000",
        auth_state="test",
        mode=OperatorMode.NORMAL,
        focus=FocusPane.CONTENT,
        section_id="dashboard",
        terminal_graphics={"no_color": True},
    )
    output = render_operator_shell(state, width=120, height=32, splash=sm)
    assert "Ananta Operator TUI" in output
