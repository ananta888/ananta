"""TUI Snapshot — CellGrid-Modell für terminalweite TUI-Snapshots.

Ermöglicht deterministische Hashing und semantische Analyse des TUI-Screens.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

_ANSI_STRIP = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])')


@dataclass
class Cell:
    x: int
    y: int
    char: str
    fg: tuple[int, int, int] | None = None
    bg: tuple[int, int, int] | None = None
    bold: bool = False
    inverse: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"x": self.x, "y": self.y, "char": self.char}
        if self.fg:
            d["fg"] = list(self.fg)
        if self.bg:
            d["bg"] = list(self.bg)
        if self.bold:
            d["bold"] = True
        if self.inverse:
            d["inverse"] = True
        return d


@dataclass
class CellGrid:
    width: int
    height: int
    cells: list[list[Cell]]  # cells[y][x]
    timestamp: float = field(default_factory=time.monotonic)
    screen_hash: str = ""

    def __post_init__(self) -> None:
        if not self.screen_hash:
            self.screen_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        canonical = json.dumps(
            [[c.to_dict() for c in row] for row in self.cells],
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:32]

    def get_cell(self, x: int, y: int) -> Cell | None:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.cells[y][x]
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "screen_hash": self.screen_hash,
            "timestamp": self.timestamp,
            "cells": [[c.to_dict() for c in row] for row in self.cells],
        }

    @staticmethod
    def from_rendered_lines(lines: list[str], *, timestamp: float | None = None) -> "CellGrid":
        """Normalisiert ANSI-freie oder ANSI-enthaltende Zeilen in ein CellGrid."""
        if not lines:
            return CellGrid(width=0, height=0, cells=[], timestamp=timestamp or time.monotonic())
        height = len(lines)
        width = max(len(_ANSI_STRIP.sub("", ln)) for ln in lines) if lines else 0
        cells: list[list[Cell]] = []
        for y, line in enumerate(lines):
            clean = _ANSI_STRIP.sub("", line)
            row: list[Cell] = []
            for x in range(width):
                char = clean[x] if x < len(clean) else " "
                row.append(Cell(x=x, y=y, char=char))
            cells.append(row)
        return CellGrid(width=width, height=height, cells=cells, timestamp=timestamp or time.monotonic())

    @staticmethod
    def empty(width: int, height: int) -> "CellGrid":
        cells = [[Cell(x=x, y=y, char=" ") for x in range(width)] for y in range(height)]
        return CellGrid(width=width, height=height, cells=cells)
