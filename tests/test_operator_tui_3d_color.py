from __future__ import annotations

from client_surfaces.operator_tui.animation3d.color import parse_color_spec, render_colored


class TestColor:
    def test_parse_rgb(self):
        rgb = parse_color_spec("0,180,80")
        assert rgb == (0, 180, 80)

    def test_parse_named(self):
        rgb = parse_color_spec("green")
        assert rgb is not None
        assert len(rgb) == 3

    def test_parse_empty(self):
        assert parse_color_spec("") is None

    def test_parse_invalid(self):
        assert parse_color_spec("not_a_color") is None

    def test_render_colored_includes_ansi(self):
        result = render_colored("X", (0, 180, 80), None)
        assert "\x1b[" in result
        assert "38;2;0;180;80" in result
        assert "X" in result
        assert result.endswith("\x1b[0m")

    def test_render_colored_with_bg(self):
        result = render_colored("X", (255, 0, 0), (0, 0, 0))
        assert "48;2;0;0;0" in result
        assert "38;2;255;0;0" in result

    def test_no_color_reset_at_end(self):
        result = render_colored("test", (100, 150, 200), None)
        assert result.endswith("\x1b[0m")
