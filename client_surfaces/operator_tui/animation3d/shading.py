from __future__ import annotations

import math

_DENSITY_CHARS = " .:oO8#@"
_SNAKE_CHARS = " .:sSoO8@"
_EDGE_CATEGORIES = {
    "h": "-",
    "v": "|",
    "f": "/",
    "b": "\\",
}


def _edge_category(angle: float) -> str:
    norm = (angle + math.pi) % (math.pi * 2)
    if norm < math.pi / 8 or norm >= 15 * math.pi / 8:
        return "h"
    if norm < 3 * math.pi / 8:
        return "f"
    if norm < 5 * math.pi / 8:
        return "v"
    if norm < 7 * math.pi / 8:
        return "b"
    if norm < 9 * math.pi / 8:
        return "h"
    if norm < 11 * math.pi / 8:
        return "f"
    if norm < 13 * math.pi / 8:
        return "v"
    return "b"


def char_for_cell(cell: dict) -> str:
    angle = cell.get("angle", 0.0)
    is_snake = cell.get("is_snake", False)
    part = cell.get("part", "")
    z = cell.get("z", 0.0)

    if is_snake:
        cat = _edge_category(angle)
        if cat == "h":
            return "~"
        if cat == "v":
            return "s"
        if cat == "f":
            return "o"
        return "c"
    else:
        cat = _edge_category(angle)
        base = _EDGE_CATEGORIES.get(cat, ".")
        return base


def density_char(depth: float, palette: str = _DENSITY_CHARS) -> str:
    clamped = max(0.0, min(1.0, depth))
    n = len(palette) - 1
    idx = int(round(clamped * n))
    return palette[min(idx, n)]
