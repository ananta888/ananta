"""Bounded maze codec, generator, solver and reference plugin."""

from __future__ import annotations

import random
from collections import deque
from typing import Any, Mapping

from worker.hrm_experiments.puzzles.common import (
    PuzzleValidationError,
    exact_accuracy,
    require_record_id,
)

_ALLOWED_CELLS = frozenset("#.SG")
_DIRECTIONS = ((-1, 0), (0, -1), (0, 1), (1, 0))


def normalize_maze(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not 2 <= len(value) <= 64:
        raise PuzzleValidationError("hrm.maze_shape_invalid")
    if not all(isinstance(row, str) for row in value):
        raise PuzzleValidationError("hrm.maze_shape_invalid")
    width = len(value[0])
    if not 2 <= width <= 64 or any(len(row) != width for row in value):
        raise PuzzleValidationError("hrm.maze_shape_invalid")
    if any(set(row).difference(_ALLOWED_CELLS) for row in value):
        raise PuzzleValidationError("hrm.maze_cell_invalid")
    joined = "".join(value)
    if joined.count("S") != 1 or joined.count("G") != 1:
        raise PuzzleValidationError("hrm.maze_endpoints_invalid")
    return tuple(value)


def solve_maze(grid: tuple[str, ...], *, node_budget: int = 4096) -> tuple[tuple[int, int], ...]:
    rows, columns = len(grid), len(grid[0])
    start = next((r, c) for r in range(rows) for c in range(columns) if grid[r][c] == "S")
    goal = next((r, c) for r in range(rows) for c in range(columns) if grid[r][c] == "G")
    queue = deque([start])
    parents: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        if visited > node_budget:
            raise PuzzleValidationError("hrm.maze_node_budget_exceeded")
        if current == goal:
            path: list[tuple[int, int]] = []
            cursor: tuple[int, int] | None = current
            while cursor is not None:
                path.append(cursor)
                cursor = parents[cursor]
            return tuple(reversed(path))
        for row_delta, column_delta in _DIRECTIONS:
            neighbor = (current[0] + row_delta, current[1] + column_delta)
            row, column = neighbor
            if (
                0 <= row < rows
                and 0 <= column < columns
                and grid[row][column] != "#"
                and neighbor not in parents
            ):
                parents[neighbor] = current
                queue.append(neighbor)
    raise PuzzleValidationError("hrm.maze_unsatisfiable")


def validate_maze_record(record: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    if set(record) != {"puzzle_id", "grid", "solution"}:
        raise PuzzleValidationError("hrm.maze_record_invalid")
    require_record_id(dict(record))
    grid = normalize_maze(record["grid"])
    raw_solution = record["solution"]
    if not isinstance(raw_solution, list) or not raw_solution:
        raise PuzzleValidationError("hrm.maze_solution_invalid")
    solution: list[tuple[int, int]] = []
    for point in raw_solution:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or any(type(value) is not int for value in point)
        ):
            raise PuzzleValidationError("hrm.maze_solution_invalid")
        solution.append((point[0], point[1]))
    expected = solve_maze(grid)
    if tuple(solution) != expected:
        raise PuzzleValidationError("hrm.maze_solution_invalid")
    return grid, expected


def generate_maze_record(seed: int, *, size: int = 9) -> dict[str, Any]:
    if not 3 <= size <= 32:
        raise ValueError("size must be between 3 and 32")
    randomizer = random.Random(seed)
    grid = [["." for _ in range(size)] for _ in range(size)]
    for row in range(size):
        for column in range(size):
            if (row == 0 or column == size - 1) or (row, column) in {(0, 0), (size - 1, size - 1)}:
                continue
            if randomizer.random() < 0.25:
                grid[row][column] = "#"
    grid[0][0] = "S"
    grid[size - 1][size - 1] = "G"
    normalized = tuple("".join(row) for row in grid)
    solution = solve_maze(normalized)
    return {
        "puzzle_id": f"maze-seed-{seed}",
        "grid": list(normalized),
        "solution": [[row, column] for row, column in solution],
    }


class MazeReferencePlugin:
    profile_id = "hrm-maze-reference-v1"
    puzzle_type = "maze"
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
                raise PuzzleValidationError("hrm.maze_record_invalid")
            grid, expected = validate_maze_record(raw_record)
            correct += int(solve_maze(grid) == expected)
        return {"metrics": exact_accuracy(correct, len(records)), "artifacts": []}


__all__ = [
    "MazeReferencePlugin",
    "generate_maze_record",
    "normalize_maze",
    "solve_maze",
    "validate_maze_record",
]
