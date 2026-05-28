"""ANSI Replay — rudimentärer ANSI-State-Tracker für PTY-Stream-zu-CellGrid-Konvertierung.

Verfolgt Cursor-Position und SGR-Attribute über einen rohen ANSI-Bytestream.
Unbekannte Sequenzen werden ignoriert, damit der Tracker nie abstürzt.
"""
from __future__ import annotations

import re
from typing import Any

from client_surfaces.operator_tui.snapshot import Cell, CellGrid

# Matches ESC followed by:
#   - CSI sequences: ESC [ params final
#   - Other two-char ESC sequences
_CSI_RE = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])')
_ESC_OTHER = re.compile(r'\x1b[^[]')


class AnsiReplayState:
    """Verfolgt den sichtbaren Bildschirminhalt durch sequentielle chunk-Anwendung."""

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self._width = width
        self._height = height
        # Buffer: list of rows, each row is list of (char, fg, bg, bold, inverse)
        self._buf: list[list[list[Any]]] = self._blank_buf()
        self._cx = 0  # cursor x
        self._cy = 0  # cursor y
        self._bold = False
        self._inverse = False
        self._fg: tuple[int, int, int] | None = None
        self._bg: tuple[int, int, int] | None = None

    def _blank_buf(self) -> list[list[list[Any]]]:
        return [[[" ", None, None, False, False] for _ in range(self._width)]
                for _ in range(self._height)]

    def _reset_attrs(self) -> None:
        self._bold = False
        self._inverse = False
        self._fg = None
        self._bg = None

    def _write_char(self, ch: str) -> None:
        if self._cy >= self._height:
            return
        if self._cx >= self._width:
            # Implicit wrap — next line
            self._cx = 0
            self._cy += 1
            if self._cy >= self._height:
                return
        self._buf[self._cy][self._cx] = [ch, self._fg, self._bg, self._bold, self._inverse]
        self._cx += 1

    def _apply_sgr(self, params: str) -> None:
        """Verarbeitet SGR (Select Graphic Rendition) — nur einfache Codes."""
        codes = [int(p) if p else 0 for p in params.split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self._reset_attrs()
            elif c == 1:
                self._bold = True
            elif c == 7:
                self._inverse = True
            elif c == 22:
                self._bold = False
            elif c == 27:
                self._inverse = False
            elif 30 <= c <= 37:
                # Standard foreground colors → mapped to approximate RGB
                self._fg = _ANSI_COLORS[c - 30]
            elif c == 39:
                self._fg = None
            elif 40 <= c <= 47:
                self._bg = _ANSI_COLORS[c - 40]
            elif c == 49:
                self._bg = None
            elif c == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
                # 256-color fg: 38;5;n — accepted but mapped to None (colour detail not needed)
                i += 2
            elif c == 48 and i + 2 < len(codes) and codes[i + 1] == 5:
                # 256-color bg: 48;5;n
                i += 2
            # Unbekannte Codes werden still ignoriert
            i += 1

    def _apply_csi(self, params: str, cmd: str) -> None:
        n = int(params) if params.isdigit() else 1
        if cmd == "A":  # cursor up
            self._cy = max(0, self._cy - n)
        elif cmd == "B":  # cursor down
            self._cy = min(self._height - 1, self._cy + n)
        elif cmd == "C":  # cursor forward
            self._cx = min(self._width - 1, self._cx + n)
        elif cmd == "D":  # cursor back
            self._cx = max(0, self._cx - n)
        elif cmd == "H" or cmd == "f":  # cursor position ESC[row;colH
            parts = params.split(";")
            row = (int(parts[0]) - 1) if len(parts) >= 1 and parts[0].isdigit() else 0
            col = (int(parts[1]) - 1) if len(parts) >= 2 and parts[1].isdigit() else 0
            self._cy = max(0, min(self._height - 1, row))
            self._cx = max(0, min(self._width - 1, col))
        elif cmd == "J":  # erase in display
            if params in ("", "0"):
                # clear from cursor to end
                for x in range(self._cx, self._width):
                    self._buf[self._cy][x] = [" ", None, None, False, False]
                for y in range(self._cy + 1, self._height):
                    self._buf[y] = [[" ", None, None, False, False] for _ in range(self._width)]
            elif params == "2":
                self._buf = self._blank_buf()
                self._cx = 0
                self._cy = 0
        elif cmd == "K":  # erase in line
            if params in ("", "0"):
                for x in range(self._cx, self._width):
                    self._buf[self._cy][x] = [" ", None, None, False, False]
            elif params == "1":
                for x in range(0, self._cx + 1):
                    self._buf[self._cy][x] = [" ", None, None, False, False]
            elif params == "2":
                self._buf[self._cy] = [[" ", None, None, False, False] for _ in range(self._width)]
        elif cmd == "m":
            self._apply_sgr(params)
        # Alle anderen CSI-Sequenzen werden ignoriert

    def apply_chunk(self, chunk: str) -> None:
        """Verarbeitet einen Rohdaten-Chunk (kann ANSI-Sequenzen enthalten)."""
        pos = 0
        while pos < len(chunk):
            if chunk[pos] == "\x1b":
                # Try CSI match first
                m = _CSI_RE.match(chunk, pos)
                if m:
                    self._apply_csi(m.group(1), m.group(2))
                    pos = m.end()
                    continue
                m2 = _ESC_OTHER.match(chunk, pos)
                if m2:
                    pos = m2.end()
                    continue
                # Lone ESC — skip
                pos += 1
            elif chunk[pos] == "\r":
                self._cx = 0
                pos += 1
            elif chunk[pos] == "\n":
                self._cy = min(self._height - 1, self._cy + 1)
                pos += 1
            elif chunk[pos] == "\b":
                self._cx = max(0, self._cx - 1)
                pos += 1
            else:
                self._write_char(chunk[pos])
                pos += 1

    def to_cell_grid(self) -> CellGrid:
        """Gibt den aktuellen Bildschirminhalt als CellGrid zurück."""
        cells: list[list[Cell]] = []
        for y, row in enumerate(self._buf):
            r: list[Cell] = []
            for x, slot in enumerate(row):
                char, fg, bg, bold, inverse = slot
                r.append(Cell(x=x, y=y, char=char, fg=fg, bg=bg, bold=bold, inverse=inverse))
            cells.append(r)
        return CellGrid(width=self._width, height=self._height, cells=cells)


# Rudimentäre ANSI-Farbpalette (Indizes 0–7)
_ANSI_COLORS: list[tuple[int, int, int]] = [
    (0, 0, 0),        # 0 black
    (170, 0, 0),      # 1 red
    (0, 170, 0),      # 2 green
    (170, 170, 0),    # 3 yellow
    (0, 0, 170),      # 4 blue
    (170, 0, 170),    # 5 magenta
    (0, 170, 170),    # 6 cyan
    (170, 170, 170),  # 7 white
]
