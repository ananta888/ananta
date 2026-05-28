"""TUI Snapshot Delta — effiziente Delta-Kodierung zwischen CellGrid-Snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.snapshot import Cell, CellGrid


@dataclass
class DirtyRegion:
    x: int; y: int; w: int; h: int

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class TuiDelta:
    previous_hash: str
    current_hash: str
    changed_cells: list[dict[str, Any]] = field(default_factory=list)
    changed_lines: list[int] = field(default_factory=list)
    dirty_regions: list[DirtyRegion] = field(default_factory=list)
    line_hashes: list[str] = field(default_factory=list)

    @property
    def changed_cell_count(self) -> int:
        return len(self.changed_cells)

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "changed_cell_count": self.changed_cell_count,
            "changed_lines": list(self.changed_lines),
            "dirty_regions": [r.to_dict() for r in self.dirty_regions],
            "changed_cells": list(self.changed_cells),
            "line_hashes": list(self.line_hashes),
        }


def _line_hash(row: list[Cell]) -> str:
    s = "".join(c.char for c in row)
    return hashlib.md5(s.encode()).hexdigest()[:16]


def _block_hash(grid: CellGrid, bx: int, by: int, bw: int, bh: int) -> str:
    chars = []
    for y in range(by, min(by + bh, grid.height)):
        for x in range(bx, min(bx + bw, grid.width)):
            chars.append(grid.cells[y][x].char)
    return hashlib.md5("".join(chars).encode()).hexdigest()[:16]


class DeltaEncoder:
    def __init__(self, block_w: int = 8, block_h: int = 4) -> None:
        self._block_w = block_w
        self._block_h = block_h
        self._prev_line_hashes: list[str] = []

    def encode(self, prev: CellGrid, curr: CellGrid) -> TuiDelta:
        delta = TuiDelta(previous_hash=prev.screen_hash, current_hash=curr.screen_hash)
        if prev.screen_hash == curr.screen_hash:
            return delta

        # Line-hashing für schnellen Vergleich
        prev_lh = [_line_hash(prev.cells[y]) for y in range(prev.height)] if prev.cells else []
        curr_lh = [_line_hash(curr.cells[y]) for y in range(curr.height)] if curr.cells else []
        delta.line_hashes = curr_lh

        changed_rows = set()
        for y in range(curr.height):
            ph = prev_lh[y] if y < len(prev_lh) else ""
            ch = curr_lh[y] if y < len(curr_lh) else ""
            if ph != ch:
                changed_rows.add(y)

        delta.changed_lines = sorted(changed_rows)

        # Zellenweise Vergleich nur in geänderten Zeilen
        for y in changed_rows:
            pw = prev.width if prev.cells else 0
            for x in range(curr.width):
                prev_cell = prev.cells[y][x] if y < prev.height and x < pw else None
                curr_cell = curr.cells[y][x]
                if prev_cell is None or prev_cell.char != curr_cell.char:
                    delta.changed_cells.append(curr_cell.to_dict())

        # Dirty regions aus benachbarten geänderten Zeilen
        delta.dirty_regions = _compute_dirty_regions(changed_rows, curr.width)
        return delta

    def apply(self, base: CellGrid, delta: TuiDelta) -> CellGrid:
        """Wendet Delta auf Basis-Snapshot an."""
        import copy
        result_cells = copy.deepcopy(base.cells)
        for cell_d in delta.changed_cells:
            x, y = cell_d["x"], cell_d["y"]
            if 0 <= y < len(result_cells) and 0 <= x < len(result_cells[y]):
                result_cells[y][x] = Cell(
                    x=x, y=y, char=cell_d["char"],
                    fg=tuple(cell_d["fg"]) if cell_d.get("fg") else None,
                    bg=tuple(cell_d["bg"]) if cell_d.get("bg") else None,
                    bold=cell_d.get("bold", False),
                    inverse=cell_d.get("inverse", False),
                )
        return CellGrid(width=base.width, height=base.height, cells=result_cells)


def _compute_dirty_regions(changed_rows: set[int], width: int) -> list[DirtyRegion]:
    if not changed_rows:
        return []
    regions: list[DirtyRegion] = []
    sorted_rows = sorted(changed_rows)
    start = sorted_rows[0]
    prev = sorted_rows[0]
    for r in sorted_rows[1:]:
        if r > prev + 1:
            regions.append(DirtyRegion(x=0, y=start, w=width, h=prev - start + 1))
            start = r
        prev = r
    regions.append(DirtyRegion(x=0, y=start, w=width, h=prev - start + 1))
    return regions
