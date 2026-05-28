from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ViewportRegion:
    x: int
    y: int
    columns: int
    rows: int
    pixel_width: int
    pixel_height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError("ViewportRegion coordinates must be non-negative")
        if self.columns <= 0 or self.rows <= 0:
            raise ValueError("ViewportRegion columns/rows must be positive")
        if self.pixel_width <= 0 or self.pixel_height <= 0:
            raise ValueError("ViewportRegion pixel size must be positive")


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def derive_pixel_size(
    *,
    columns: int,
    rows: int,
    terminal_pixel_width: int | None = None,
    terminal_pixel_height: int | None = None,
    terminal_columns: int | None = None,
    terminal_rows: int | None = None,
    default_pixel_width: int = 800,
    default_pixel_height: int = 450,
    max_pixel_width: int = 1280,
    max_pixel_height: int = 720,
) -> tuple[int, int]:
    if columns <= 0 or rows <= 0:
        raise ValueError("columns/rows must be positive")
    if default_pixel_width <= 0 or default_pixel_height <= 0:
        raise ValueError("default pixel size must be positive")
    if max_pixel_width <= 0 or max_pixel_height <= 0:
        raise ValueError("max pixel size must be positive")

    can_compute_from_terminal = (
        isinstance(terminal_pixel_width, int)
        and isinstance(terminal_pixel_height, int)
        and isinstance(terminal_columns, int)
        and isinstance(terminal_rows, int)
        and terminal_pixel_width > 0
        and terminal_pixel_height > 0
        and terminal_columns > 0
        and terminal_rows > 0
    )
    if can_compute_from_terminal:
        cell_px_w = max(1, terminal_pixel_width // terminal_columns)
        cell_px_h = max(1, terminal_pixel_height // terminal_rows)
        width = columns * cell_px_w
        height = rows * cell_px_h
    else:
        width = default_pixel_width
        height = default_pixel_height
    return (
        _clamp(width, minimum=1, maximum=max_pixel_width),
        _clamp(height, minimum=1, maximum=max_pixel_height),
    )

