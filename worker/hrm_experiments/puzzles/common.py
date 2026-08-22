"""Shared bounded puzzle validation primitives."""

from __future__ import annotations

from typing import Any, Sequence


class PuzzleValidationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def require_record_id(record: dict[str, Any]) -> str:
    record_id = record.get("puzzle_id")
    if (
        not isinstance(record_id, str)
        or not record_id
        or len(record_id) > 128
    ):
        raise PuzzleValidationError("hrm.puzzle_id_invalid")
    return record_id


def require_color_grid(
    value: Any,
    *,
    max_rows: int = 30,
    max_columns: int = 30,
) -> tuple[tuple[int, ...], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= max_rows:
        raise PuzzleValidationError("hrm.arc_grid_invalid")
    rows: list[tuple[int, ...]] = []
    width: int | None = None
    for raw_row in value:
        if not isinstance(raw_row, list) or not 1 <= len(raw_row) <= max_columns:
            raise PuzzleValidationError("hrm.arc_grid_invalid")
        if width is None:
            width = len(raw_row)
        if len(raw_row) != width:
            raise PuzzleValidationError("hrm.arc_grid_invalid")
        if any(type(cell) is not int or not 0 <= cell <= 9 for cell in raw_row):
            raise PuzzleValidationError("hrm.arc_color_invalid")
        rows.append(tuple(raw_row))
    return tuple(rows)


def exact_accuracy(correct: int, total: int) -> list[dict[str, Any]]:
    if total < 1:
        raise PuzzleValidationError("hrm.dataset_empty")
    accuracy = correct / total
    return [
        {"name": "loss", "value": 1.0 - accuracy, "unit": "scalar"},
        {"name": "exact_accuracy", "value": accuracy, "unit": "ratio"},
        {"name": "step", "value": total, "unit": "count"},
    ]


def rectangular_shape(grid: Sequence[Sequence[Any]]) -> tuple[int, int]:
    return len(grid), len(grid[0]) if grid else 0


__all__ = [
    "PuzzleValidationError",
    "exact_accuracy",
    "rectangular_shape",
    "require_color_grid",
    "require_record_id",
]
