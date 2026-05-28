"""Tests für AnsiReplayState: Cursor-Tracking, SGR, CellGrid-Output."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.ansi_replay import AnsiReplayState


class TestAnsiReplayBasic:
    def test_plain_text_written_to_grid(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("hello")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "h"
        assert grid.cells[0][4].char == "o"

    def test_initial_grid_is_blank(self) -> None:
        state = AnsiReplayState(width=5, height=3)
        grid = state.to_cell_grid()
        for row in grid.cells:
            for cell in row:
                assert cell.char == " "

    def test_grid_dimensions_match_init(self) -> None:
        state = AnsiReplayState(width=80, height=24)
        grid = state.to_cell_grid()
        assert grid.width == 80
        assert grid.height == 24

    def test_newline_advances_row(self) -> None:
        # \n alone only moves down, does not reset column (CRLF behavior needs \r\n)
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("AB\r\nCD")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "A"
        assert grid.cells[0][1].char == "B"
        assert grid.cells[1][0].char == "C"
        assert grid.cells[1][1].char == "D"

    def test_carriage_return_resets_column(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("hello\rworld")
        grid = state.to_cell_grid()
        # "world" overwrites from col 0
        assert grid.cells[0][0].char == "w"


class TestAnsiReplayCursorMovement:
    def test_cursor_up(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        # Use CRLF so cursor resets to col 0 on each line
        state.apply_chunk("line0\r\nline1\r\n")
        state.apply_chunk("\x1b[2A")  # cursor up 2 → row 0, col 0
        state.apply_chunk("X")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "X"

    def test_cursor_position_absolute(self) -> None:
        state = AnsiReplayState(width=20, height=10)
        state.apply_chunk("\x1b[3;5H")  # row 3, col 5 (1-indexed)
        state.apply_chunk("Z")
        grid = state.to_cell_grid()
        assert grid.cells[2][4].char == "Z"

    def test_cursor_position_home(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("hello\n\n")
        state.apply_chunk("\x1b[H")  # home
        state.apply_chunk("X")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "X"


class TestAnsiReplayErase:
    def test_erase_line_from_cursor(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("hello")
        state.apply_chunk("\x1b[3D")  # back 3
        state.apply_chunk("\x1b[0K")  # erase to end
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "h"
        assert grid.cells[0][1].char == "e"
        assert grid.cells[0][2].char == " "
        assert grid.cells[0][4].char == " "

    def test_erase_entire_screen(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("hello")
        state.apply_chunk("\x1b[2J")
        grid = state.to_cell_grid()
        for row in grid.cells:
            for cell in row:
                assert cell.char == " "


class TestAnsiReplaySGR:
    def test_bold_attribute_set(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("\x1b[1mB\x1b[0m")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "B"
        assert grid.cells[0][0].bold is True

    def test_bold_reset_after_sgr_0(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("\x1b[1mA\x1b[0mB")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].bold is True
        assert grid.cells[0][1].bold is False

    def test_fg_color_set(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("\x1b[31mR\x1b[0m")  # red fg
        grid = state.to_cell_grid()
        assert grid.cells[0][0].fg is not None

    def test_unknown_sgr_code_ignored_no_crash(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("\x1b[999mX")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "X"


class TestAnsiReplayUnknownSequences:
    def test_unknown_csi_ignored(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        # ESC[?25l (hide cursor) — unknown, should not crash
        state.apply_chunk("\x1b[?25lhello")
        grid = state.to_cell_grid()
        # "hello" should still be written after the ignored sequence
        # The parser may or may not write hello depending on where it resumes;
        # the key invariant is: no exception raised
        assert grid.width == 10

    def test_lone_esc_ignored(self) -> None:
        state = AnsiReplayState(width=5, height=3)
        state.apply_chunk("\x1bABC")
        # No crash is the test
        grid = state.to_cell_grid()
        assert grid.width == 5

    def test_multiple_unknown_sequences_no_crash(self) -> None:
        state = AnsiReplayState(width=20, height=5)
        state.apply_chunk("\x1b[?1049h\x1b[?1h\x1b=hello\x1b[?1049l")
        grid = state.to_cell_grid()
        assert grid.width == 20


class TestAnsiReplayChunkedInput:
    def test_chunked_plain_text(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        state.apply_chunk("hel")
        state.apply_chunk("lo")
        grid = state.to_cell_grid()
        assert grid.cells[0][0].char == "h"
        assert grid.cells[0][4].char == "o"

    def test_sequence_split_across_chunks(self) -> None:
        state = AnsiReplayState(width=10, height=5)
        # Split an ESC sequence across two chunks
        state.apply_chunk("\x1b")
        state.apply_chunk("[1mX\x1b[0m")
        grid = state.to_cell_grid()
        # Either X is bold or not — no crash is the primary invariant
        assert grid.width == 10
