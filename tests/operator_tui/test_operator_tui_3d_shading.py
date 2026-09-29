from __future__ import annotations

import math

from client_surfaces.operator_tui.animation3d.shading import char_for_cell, density_char


class TestShading:
    def test_char_for_a_line(self):
        ch = char_for_cell({"angle": 0.0, "is_snake": False, "part": "left_leg", "z": 0.0})
        assert isinstance(ch, str)
        assert len(ch) == 1
        # horizontal edge
        assert ch == "-" or ch == "|" or ch == "/" or ch == "\\"

    def test_char_for_vertical_line(self):
        ch = char_for_cell({"angle": math.pi / 2, "is_snake": False, "part": "left_leg", "z": 0.0})
        assert ch in ("-", "|", "/", "\\")

    def test_snake_characters(self):
        ch = char_for_cell({"angle": 0.0, "is_snake": True, "part": "snake_body", "z": 0.0})
        assert ch in ("~", "s", "o", "c")

    def test_density_char_bounds(self):
        assert density_char(0.0) == " "
        assert density_char(1.0) == "@"

    def test_density_char_mid(self):
        ch = density_char(0.5)
        assert ch in " .:oO8#@"

    def test_ascii_only(self):
        for angle in [0.0, math.pi / 4, math.pi / 2, math.pi]:
            ch = char_for_cell({"angle": angle, "is_snake": False, "part": "test", "z": 0.0})
            assert ord(ch) < 128
