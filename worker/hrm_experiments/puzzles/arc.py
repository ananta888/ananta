"""Bounded ARC codec and deterministic transformation baseline."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from worker.hrm_experiments.puzzles.common import (
    PuzzleValidationError,
    exact_accuracy,
    require_color_grid,
    require_record_id,
)

Grid = tuple[tuple[int, ...], ...]


def _identity(grid: Grid) -> Grid:
    return grid


def _rotate_right(grid: Grid) -> Grid:
    return tuple(tuple(row[index] for row in reversed(grid)) for index in range(len(grid[0])))


def _rotate_180(grid: Grid) -> Grid:
    return tuple(tuple(reversed(row)) for row in reversed(grid))


def _rotate_left(grid: Grid) -> Grid:
    return tuple(tuple(row[index] for row in grid) for index in reversed(range(len(grid[0]))))


def _flip_horizontal(grid: Grid) -> Grid:
    return tuple(tuple(reversed(row)) for row in grid)


def _flip_vertical(grid: Grid) -> Grid:
    return tuple(reversed(grid))


def _transpose(grid: Grid) -> Grid:
    return tuple(tuple(row[index] for row in grid) for index in range(len(grid[0])))


_TRANSFORMS: tuple[Callable[[Grid], Grid], ...] = (
    _identity,
    _rotate_right,
    _rotate_180,
    _rotate_left,
    _flip_horizontal,
    _flip_vertical,
    _transpose,
)


def infer_arc_output(training: tuple[tuple[Grid, Grid], ...], test_input: Grid) -> Grid | None:
    for transform in _TRANSFORMS:
        if all(transform(source) == target for source, target in training):
            return transform(test_input)
    color_mapping: dict[int, int] = {}
    for source, target in training:
        if len(source) != len(target) or len(source[0]) != len(target[0]):
            color_mapping = {}
            break
        for source_row, target_row in zip(source, target):
            for source_color, target_color in zip(source_row, target_row):
                existing = color_mapping.setdefault(source_color, target_color)
                if existing != target_color:
                    color_mapping = {}
                    break
            if not color_mapping:
                break
        if not color_mapping:
            break
    if color_mapping:
        return tuple(
            tuple(color_mapping.get(color, color) for color in row)
            for row in test_input
        )
    return None


def validate_arc_record(record: Mapping[str, Any]) -> tuple[tuple[tuple[Grid, Grid], ...], Grid, Grid]:
    if set(record) != {"puzzle_id", "train", "test"}:
        raise PuzzleValidationError("hrm.arc_record_invalid")
    require_record_id(dict(record))
    raw_training = record["train"]
    if not isinstance(raw_training, list) or not 1 <= len(raw_training) <= 10:
        raise PuzzleValidationError("hrm.arc_training_invalid")
    training: list[tuple[Grid, Grid]] = []
    for pair in raw_training:
        if not isinstance(pair, Mapping) or set(pair) != {"input", "output"}:
            raise PuzzleValidationError("hrm.arc_training_invalid")
        training.append(
            (require_color_grid(pair["input"]), require_color_grid(pair["output"]))
        )
    test = record["test"]
    if not isinstance(test, Mapping) or set(test) != {"input", "output"}:
        raise PuzzleValidationError("hrm.arc_test_invalid")
    return tuple(training), require_color_grid(test["input"]), require_color_grid(test["output"])


class ArcReferencePlugin:
    profile_id = "hrm-arc-reference-v1"
    puzzle_type = "arc"
    modes = frozenset({"mock", "train", "inference"})

    def execute(
        self,
        _run_request: Mapping[str, Any],
        dataset: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        records = dataset["records"]
        correct = 0
        for raw_record in records:
            if not isinstance(raw_record, Mapping):
                raise PuzzleValidationError("hrm.arc_record_invalid")
            training, test_input, expected = validate_arc_record(raw_record)
            correct += int(infer_arc_output(training, test_input) == expected)
        return {"metrics": exact_accuracy(correct, len(records)), "artifacts": []}


__all__ = ["ArcReferencePlugin", "infer_arc_output", "validate_arc_record"]
