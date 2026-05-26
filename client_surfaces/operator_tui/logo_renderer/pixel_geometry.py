from __future__ import annotations

import os


def terminal_cell_pixels(env: dict[str, str] | None = None) -> tuple[int, int]:
    values = env or os.environ
    try:
        cell_w = int(values.get("ANANTA_TUI_CELL_WIDTH_PX", "8"))
    except ValueError:
        cell_w = 8
    try:
        cell_h = int(values.get("ANANTA_TUI_CELL_HEIGHT_PX", "16"))
    except ValueError:
        cell_h = 16
    return max(4, min(32, cell_w)), max(6, min(48, cell_h))


def map_cells_to_pixels(columns: int, rows: int, *, env: dict[str, str] | None = None) -> tuple[int, int]:
    cell_w, cell_h = terminal_cell_pixels(env)
    return max(2, int(columns) * cell_w), max(2, int(rows) * cell_h)


def fit_frame_size_to_terminal(
    *,
    columns: int,
    rows: int,
    quality: str = "interactive",
    env: dict[str, str] | None = None,
) -> tuple[int, int]:
    term_w, term_h = map_cells_to_pixels(columns, rows, env=env)
    quality_key = (quality or "interactive").strip().lower()
    base = (640, 360) if quality_key in {"high", "ultra"} else (480, 270)
    width = min(term_w, base[0])
    height = min(term_h, base[1])
    if width < 64 or height < 64:
        width = max(width, min(64, term_w))
        height = max(height, min(64, term_h))
    return width, height


def header_logo_target_pixels(*, width_cells: int, height_lines: int, oversampling: int = 2) -> tuple[int, int]:
    safe_w = max(2, int(width_cells))
    safe_h = max(2, int(height_lines))
    factor = max(1, min(8, int(oversampling)))
    return safe_w * factor, max(safe_h * factor, safe_h * 2)
