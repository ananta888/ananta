from __future__ import annotations

import os

from client_surfaces.operator_tui.animation3d.capabilities import detect_3d_capability


class TestCapabilityDetection:
    def test_enabled_in_tty_large_terminal(self):
        cap = detect_3d_capability(
            env={},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
            no_color=False,
        )
        assert cap.enabled
        assert cap.reason_code == "ok"

    def test_disabled_if_no_tty(self):
        cap = detect_3d_capability(
            env={},
            terminal_width=120,
            terminal_height=32,
            is_tty=False,
        )
        assert not cap.enabled
        assert cap.reason_code == "no_tty"

    def test_disabled_if_too_small(self):
        cap = detect_3d_capability(
            env={},
            terminal_width=60,
            terminal_height=12,
            is_tty=True,
        )
        assert not cap.enabled
        assert cap.reason_code == "too_small"

    def test_disabled_by_env_var(self):
        cap = detect_3d_capability(
            env={"ANANTA_TUI_3D": "0"},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
        )
        assert not cap.enabled
        assert cap.reason_code == "disabled_by_env"

    def test_disabled_by_splash_env(self):
        cap = detect_3d_capability(
            env={"ANANTA_TUI_SPLASH": "0"},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
        )
        assert not cap.enabled
        assert cap.reason_code == "disabled_by_splash_env"

    def test_mono_mode_when_no_color(self):
        cap = detect_3d_capability(
            env={"NO_COLOR": "1"},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
        )
        assert cap.enabled
        assert cap.color_mode == "mono"

    def test_preset_from_env(self):
        cap = detect_3d_capability(
            env={"ANANTA_TUI_3D_PRESET": "snake_orbit"},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
        )
        assert cap.preset_name == "snake_orbit"

    def test_fps_from_env(self):
        cap = detect_3d_capability(
            env={"ANANTA_TUI_3D_FPS": "30"},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
        )
        assert cap.max_fps == 30

    def test_duration_from_env(self):
        cap = detect_3d_capability(
            env={"ANANTA_TUI_3D_DURATION_MS": "3000"},
            terminal_width=120,
            terminal_height=32,
            is_tty=True,
        )
        assert cap.duration_ms == 3000
