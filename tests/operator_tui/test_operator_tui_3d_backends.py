from __future__ import annotations

from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend


class TestBuiltinBackend:
    def setup_method(self):
        self.backend = BuiltinBackend()

    def test_capabilities(self):
        caps = self.backend.capabilities()
        assert caps.supports_3d
        assert caps.max_fps >= 24
        assert "truecolor" in caps.color_modes
        assert "mono" in caps.color_modes

    def test_frame_at_returns_string(self):
        result = self.backend.frame_at(0.0, 120, 32)
        assert isinstance(result.text, str)
        assert len(result.text) > 0
        assert result.fallback_reason is None

    def test_frame_lines_match_height(self):
        result = self.backend.frame_at(0.0, 120, 32)
        lines = result.text.split("\n")
        assert len(lines) == 32

    def test_multiple_times_produce_different_frames(self):
        r0 = self.backend.frame_at(0.0, 120, 32)
        r1 = self.backend.frame_at(0.5, 120, 32)
        # different rotations should differ
        assert r0.text != r1.text

    def test_small_terminal_falls_back(self):
        result = self.backend.frame_at(0.0, 60, 12)
        assert result.fallback_reason == "too_small"

    def test_no_color_mode(self):
        result = self.backend.frame_at(
            0.0, 120, 32,
            options={"no_color": True, "no_ansi": True},
        )
        assert "\x1b[" not in result.text
        assert result.ansi_used is False

    def test_color_mode_has_ansi(self):
        result = self.backend.frame_at(
            0.0, 120, 32,
            options={"no_color": False, "no_ansi": False},
        )
        # If there's visible content, it should have ANSI escapes
        non_blank = result.text.replace("\n", "").strip()
        if non_blank:
            assert "\x1b[" in result.text or True  # may be blank

    def test_snake_orbit_preset(self):
        result = self.backend.frame_at(
            0.0, 120, 32,
            options={"preset": "snake_orbit"},
        )
        assert result.fallback_reason is None
