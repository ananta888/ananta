"""Tests für DeltaEncoder: Encoding, Anwendung (apply), DirtyRegions."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.snapshot import CellGrid
from client_surfaces.operator_tui.snapshot_delta import DeltaEncoder, DirtyRegion, TuiDelta, _compute_dirty_regions


class TestDeltaEncoderNoChange:
    def test_identical_grids_produce_empty_delta(self) -> None:
        grid = CellGrid.from_rendered_lines(["hello"])
        enc = DeltaEncoder()
        delta = enc.encode(grid, grid)
        assert delta.changed_cell_count == 0
        assert delta.changed_lines == []
        assert delta.dirty_regions == []

    def test_same_content_different_objects_produce_empty_delta(self) -> None:
        g1 = CellGrid.from_rendered_lines(["hello world"])
        g2 = CellGrid.from_rendered_lines(["hello world"])
        enc = DeltaEncoder()
        delta = enc.encode(g1, g2)
        assert delta.changed_cell_count == 0


class TestDeltaEncoderChanges:
    def test_single_char_change_detected(self) -> None:
        prev = CellGrid.from_rendered_lines(["hello"])
        curr = CellGrid.from_rendered_lines(["hXllo"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        assert delta.changed_cell_count >= 1
        chars = [c["char"] for c in delta.changed_cells]
        assert "X" in chars

    def test_full_line_change(self) -> None:
        prev = CellGrid.from_rendered_lines(["aaaaa", "bbbbb"])
        curr = CellGrid.from_rendered_lines(["aaaaa", "ccccc"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        assert 1 in delta.changed_lines
        assert 0 not in delta.changed_lines

    def test_all_lines_changed(self) -> None:
        prev = CellGrid.from_rendered_lines(["aaa", "bbb"])
        curr = CellGrid.from_rendered_lines(["xxx", "yyy"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        assert set(delta.changed_lines) == {0, 1}

    def test_changed_cells_contain_correct_coords(self) -> None:
        prev = CellGrid.from_rendered_lines(["abc"])
        curr = CellGrid.from_rendered_lines(["aXc"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        changed = {(c["x"], c["y"]) for c in delta.changed_cells}
        assert (1, 0) in changed

    def test_hashes_reflected_in_delta(self) -> None:
        prev = CellGrid.from_rendered_lines(["old"])
        curr = CellGrid.from_rendered_lines(["new"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        assert delta.previous_hash == prev.screen_hash
        assert delta.current_hash == curr.screen_hash


class TestDeltaApply:
    def test_apply_reconstructs_current_grid(self) -> None:
        prev = CellGrid.from_rendered_lines(["hello world"])
        curr = CellGrid.from_rendered_lines(["hello XXXXX"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        reconstructed = enc.apply(prev, delta)
        # Reconstructed chars should match curr
        for x in range(curr.width):
            assert reconstructed.cells[0][x].char == curr.cells[0][x].char

    def test_apply_empty_delta_leaves_grid_unchanged(self) -> None:
        grid = CellGrid.from_rendered_lines(["unchanged"])
        enc = DeltaEncoder()
        delta = enc.encode(grid, grid)
        result = enc.apply(grid, delta)
        for x in range(grid.width):
            assert result.cells[0][x].char == grid.cells[0][x].char

    def test_apply_multiline(self) -> None:
        prev_lines = ["line one  ", "line two  ", "line three"]
        curr_lines = ["line one  ", "CHANGED   ", "line three"]
        prev = CellGrid.from_rendered_lines(prev_lines)
        curr = CellGrid.from_rendered_lines(curr_lines)
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        result = enc.apply(prev, delta)
        result_line1 = "".join(c.char for c in result.cells[1])
        assert result_line1.startswith("CHANGED")


class TestDeltaToDictSchema:
    def test_to_dict_has_required_keys(self) -> None:
        prev = CellGrid.from_rendered_lines(["a"])
        curr = CellGrid.from_rendered_lines(["b"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        d = delta.to_dict()
        required = {"previous_hash", "current_hash", "changed_cell_count",
                    "changed_lines", "dirty_regions", "changed_cells", "line_hashes"}
        assert required <= set(d.keys())

    def test_to_dict_line_hashes_length_matches_height(self) -> None:
        prev = CellGrid.from_rendered_lines(["aaa", "bbb"])
        curr = CellGrid.from_rendered_lines(["aaa", "ccc"])
        enc = DeltaEncoder()
        delta = enc.encode(prev, curr)
        d = delta.to_dict()
        assert len(d["line_hashes"]) == 2


class TestDirtyRegions:
    def test_single_changed_row(self) -> None:
        regions = _compute_dirty_regions({3}, width=80)
        assert len(regions) == 1
        assert regions[0].y == 3
        assert regions[0].h == 1
        assert regions[0].w == 80

    def test_contiguous_rows_merged(self) -> None:
        regions = _compute_dirty_regions({2, 3, 4}, width=80)
        assert len(regions) == 1
        assert regions[0].y == 2
        assert regions[0].h == 3

    def test_non_contiguous_rows_separate_regions(self) -> None:
        regions = _compute_dirty_regions({0, 5}, width=80)
        assert len(regions) == 2

    def test_empty_set_no_regions(self) -> None:
        regions = _compute_dirty_regions(set(), width=80)
        assert regions == []

    def test_dirty_region_to_dict(self) -> None:
        r = DirtyRegion(x=0, y=5, w=120, h=3)
        d = r.to_dict()
        assert d == {"x": 0, "y": 5, "w": 120, "h": 3}
