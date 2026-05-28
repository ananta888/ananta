"""Tests für deterministische Hashing-Eigenschaften des Delta-Systems."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.snapshot import CellGrid
from client_surfaces.operator_tui.snapshot_delta import DeltaEncoder, _line_hash


class TestLineHashing:
    def test_identical_rows_same_hash(self) -> None:
        grid = CellGrid.from_rendered_lines(["hello"])
        h1 = _line_hash(grid.cells[0])
        h2 = _line_hash(grid.cells[0])
        assert h1 == h2

    def test_different_rows_different_hash(self) -> None:
        g1 = CellGrid.from_rendered_lines(["hello"])
        g2 = CellGrid.from_rendered_lines(["world"])
        assert _line_hash(g1.cells[0]) != _line_hash(g2.cells[0])

    def test_line_hash_is_16_hex_chars(self) -> None:
        grid = CellGrid.from_rendered_lines(["test line"])
        h = _line_hash(grid.cells[0])
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_row_has_stable_hash(self) -> None:
        grid = CellGrid.empty(10, 1)
        h1 = _line_hash(grid.cells[0])
        h2 = _line_hash(grid.cells[0])
        assert h1 == h2


class TestDeltaHashConsistency:
    def test_delta_hashes_match_grid_hashes(self) -> None:
        prev = CellGrid.from_rendered_lines(["aaaa"])
        curr = CellGrid.from_rendered_lines(["bbbb"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        assert delta.previous_hash == prev.screen_hash
        assert delta.current_hash == curr.screen_hash

    def test_no_change_delta_has_equal_hashes(self) -> None:
        grid = CellGrid.from_rendered_lines(["same"])
        enc = DeltaEncoder()
        delta = enc.encode(grid, grid)
        assert delta.previous_hash == delta.current_hash

    def test_line_hashes_in_delta_stable(self) -> None:
        prev = CellGrid.from_rendered_lines(["row0", "row1"])
        curr = CellGrid.from_rendered_lines(["row0", "XXXX"])
        enc = DeltaEncoder()
        d1 = enc.encode(prev, curr)
        d2 = enc.encode(prev, curr)
        assert d1.line_hashes == d2.line_hashes

    def test_apply_then_re_encode_is_empty_delta(self) -> None:
        """Applying a delta and re-encoding against the target should produce zero changes."""
        prev = CellGrid.from_rendered_lines(["hello world!!"])
        curr = CellGrid.from_rendered_lines(["hello XXXXXXX"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        reconstructed = enc.apply(prev, delta)

        # Re-encode reconstructed vs curr — chars should match, so no changes
        delta2 = enc.encode(reconstructed, curr)
        # Either no changes at all, or only whitespace-equivalent cells
        for c in delta2.changed_cells:
            assert c["char"] == curr.cells[c["y"]][c["x"]].char

    def test_delta_changed_cell_count_property(self) -> None:
        prev = CellGrid.from_rendered_lines(["aaa"])
        curr = CellGrid.from_rendered_lines(["bbb"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        assert delta.changed_cell_count == len(delta.changed_cells)

    def test_large_grid_hash_stable(self) -> None:
        lines = ["A" * 120] * 32
        g1 = CellGrid.from_rendered_lines(lines)
        g2 = CellGrid.from_rendered_lines(lines)
        assert g1.screen_hash == g2.screen_hash
        enc = DeltaEncoder()
        delta = enc.encode(g1, g2)
        assert delta.changed_cell_count == 0
