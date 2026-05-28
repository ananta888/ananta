"""Tests für das CellGrid-Modell: leere Screens, Unicode, 120x32, deterministisches Hashing, ANSI-Stripping."""
from __future__ import annotations

import time

import pytest

from client_surfaces.operator_tui.snapshot import Cell, CellGrid


class TestEmptyScreens:
    def test_empty_factory_produces_zero_dimensions(self) -> None:
        grid = CellGrid.from_rendered_lines([])
        assert grid.width == 0
        assert grid.height == 0
        assert grid.cells == []

    def test_empty_grid_has_stable_hash(self) -> None:
        g1 = CellGrid.from_rendered_lines([])
        g2 = CellGrid.from_rendered_lines([])
        assert g1.screen_hash == g2.screen_hash

    def test_empty_factory_explicit(self) -> None:
        grid = CellGrid.empty(0, 0)
        assert grid.width == 0
        assert grid.height == 0

    def test_blank_grid_all_spaces(self) -> None:
        grid = CellGrid.empty(5, 3)
        for row in grid.cells:
            for cell in row:
                assert cell.char == " "


class TestUnicodeHandling:
    def test_single_unicode_char_stored(self) -> None:
        grid = CellGrid.from_rendered_lines(["αβγ"])
        assert grid.cells[0][0].char == "α"
        assert grid.cells[0][1].char == "β"
        assert grid.cells[0][2].char == "γ"

    def test_emoji_treated_as_single_char(self) -> None:
        # Emoji counts as one character in Python len()
        grid = CellGrid.from_rendered_lines(["A🐍B"])
        assert grid.cells[0][0].char == "A"
        assert grid.cells[0][1].char == "🐍"
        assert grid.cells[0][2].char == "B"

    def test_unicode_hash_differs_from_ascii(self) -> None:
        g_ascii = CellGrid.from_rendered_lines(["abc"])
        g_unicode = CellGrid.from_rendered_lines(["αβγ"])
        assert g_ascii.screen_hash != g_unicode.screen_hash


class TestFixedSize120x32:
    def test_120x32_dimensions(self) -> None:
        lines = ["x" * 120] * 32
        grid = CellGrid.from_rendered_lines(lines)
        assert grid.width == 120
        assert grid.height == 32

    def test_120x32_all_cells_accessible(self) -> None:
        lines = [str(y % 10) * 120 for y in range(32)]
        grid = CellGrid.from_rendered_lines(lines)
        for y in range(32):
            for x in range(120):
                cell = grid.get_cell(x, y)
                assert cell is not None
                assert cell.char == str(y % 10)

    def test_120x32_get_cell_out_of_bounds_returns_none(self) -> None:
        grid = CellGrid.empty(120, 32)
        assert grid.get_cell(120, 0) is None
        assert grid.get_cell(0, 32) is None
        assert grid.get_cell(-1, 0) is None

    def test_120x32_to_dict_shape(self) -> None:
        grid = CellGrid.empty(120, 32)
        d = grid.to_dict()
        assert d["width"] == 120
        assert d["height"] == 32
        assert len(d["cells"]) == 32
        assert len(d["cells"][0]) == 120


class TestDeterministicHash:
    def test_same_content_same_hash(self) -> None:
        lines = ["hello world", "second line"]
        g1 = CellGrid.from_rendered_lines(lines, timestamp=1.0)
        g2 = CellGrid.from_rendered_lines(lines, timestamp=2.0)
        # Hash must not depend on timestamp
        assert g1.screen_hash == g2.screen_hash

    def test_different_content_different_hash(self) -> None:
        g1 = CellGrid.from_rendered_lines(["aaaa"])
        g2 = CellGrid.from_rendered_lines(["bbbb"])
        assert g1.screen_hash != g2.screen_hash

    def test_hash_is_32_hex_chars(self) -> None:
        grid = CellGrid.from_rendered_lines(["test"])
        assert len(grid.screen_hash) == 32
        assert all(c in "0123456789abcdef" for c in grid.screen_hash)

    def test_hash_stable_across_repeated_construction(self) -> None:
        lines = ["line one", "line two", "line three"]
        hashes = {CellGrid.from_rendered_lines(lines).screen_hash for _ in range(5)}
        assert len(hashes) == 1

    def test_trailing_space_changes_hash(self) -> None:
        g1 = CellGrid.from_rendered_lines(["abc"])
        g2 = CellGrid.from_rendered_lines(["abc "])
        # Different widths → different content → different hash
        assert g1.screen_hash != g2.screen_hash


class TestAnsiStripping:
    def test_strips_color_codes(self) -> None:
        grid = CellGrid.from_rendered_lines(["\x1b[31mRED\x1b[0m"])
        assert grid.cells[0][0].char == "R"
        assert grid.cells[0][1].char == "E"
        assert grid.cells[0][2].char == "D"
        assert grid.width == 3

    def test_strips_bold_sequence(self) -> None:
        grid = CellGrid.from_rendered_lines(["\x1b[1mBOLD\x1b[0m"])
        assert grid.width == 4
        assert "".join(c.char for c in grid.cells[0]) == "BOLD"

    def test_strips_cursor_movement(self) -> None:
        # ESC[H is cursor home — should be stripped, leaving empty width
        grid = CellGrid.from_rendered_lines(["\x1b[H"])
        assert grid.width == 0

    def test_ansi_stripped_hash_equals_plain_hash(self) -> None:
        plain = CellGrid.from_rendered_lines(["hello"])
        ansi = CellGrid.from_rendered_lines(["\x1b[32mhello\x1b[0m"])
        # Both should resolve to the same characters
        assert plain.screen_hash == ansi.screen_hash

    def test_mixed_ansi_and_unicode(self) -> None:
        grid = CellGrid.from_rendered_lines(["\x1b[1mα\x1b[0mβ"])
        assert grid.cells[0][0].char == "α"
        assert grid.cells[0][1].char == "β"


class TestCellToDict:
    def test_minimal_cell(self) -> None:
        c = Cell(x=0, y=0, char="A")
        d = c.to_dict()
        assert d == {"x": 0, "y": 0, "char": "A"}

    def test_full_cell(self) -> None:
        c = Cell(x=1, y=2, char="X", fg=(255, 0, 0), bg=(0, 0, 255), bold=True, inverse=True)
        d = c.to_dict()
        assert d["fg"] == [255, 0, 0]
        assert d["bg"] == [0, 0, 255]
        assert d["bold"] is True
        assert d["inverse"] is True

    def test_false_flags_not_in_dict(self) -> None:
        c = Cell(x=0, y=0, char="Z", bold=False, inverse=False)
        d = c.to_dict()
        assert "bold" not in d
        assert "inverse" not in d
        assert "fg" not in d
        assert "bg" not in d
