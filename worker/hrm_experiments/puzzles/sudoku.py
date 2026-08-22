"""Deterministic Sudoku codec, generator, solver and reference plugin."""

from __future__ import annotations

import random
from typing import Any, Mapping

from worker.hrm_experiments.puzzles.common import (
    PuzzleValidationError,
    exact_accuracy,
    require_record_id,
)

_DIGITS = frozenset(range(1, 10))


def normalize_sudoku_grid(value: Any, *, allow_zero: bool) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != 9:
        raise PuzzleValidationError("hrm.sudoku_shape_invalid")
    flattened: list[int] = []
    for row in value:
        if not isinstance(row, list) or len(row) != 9:
            raise PuzzleValidationError("hrm.sudoku_shape_invalid")
        for cell in row:
            if type(cell) is not int or cell < (0 if allow_zero else 1) or cell > 9:
                raise PuzzleValidationError("hrm.sudoku_value_invalid")
            flattened.append(cell)
    return tuple(flattened)


def validate_sudoku_record(record: Mapping[str, Any]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if set(record) != {"puzzle_id", "puzzle", "solution"}:
        raise PuzzleValidationError("hrm.sudoku_record_invalid")
    require_record_id(dict(record))
    puzzle = normalize_sudoku_grid(record["puzzle"], allow_zero=True)
    solution = normalize_sudoku_grid(record["solution"], allow_zero=False)
    if not _valid_completed_grid(solution):
        raise PuzzleValidationError("hrm.sudoku_solution_invalid")
    if any(clue and clue != solution[index] for index, clue in enumerate(puzzle)):
        raise PuzzleValidationError("hrm.sudoku_clue_conflict")
    if not _valid_partial_grid(puzzle):
        raise PuzzleValidationError("hrm.sudoku_clue_conflict")
    return puzzle, solution


def solve_sudoku(puzzle: tuple[int, ...], *, node_budget: int = 100_000) -> tuple[int, ...]:
    grid = list(puzzle)
    nodes = 0

    def search() -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > node_budget:
            raise PuzzleValidationError("hrm.sudoku_node_budget_exceeded")
        best_index = -1
        best_candidates: set[int] | None = None
        for index, value in enumerate(grid):
            if value:
                continue
            candidates = _candidates(grid, index)
            if not candidates:
                return False
            if best_candidates is None or len(candidates) < len(best_candidates):
                best_index = index
                best_candidates = candidates
                if len(candidates) == 1:
                    break
        if best_candidates is None:
            return True
        for candidate in sorted(best_candidates):
            grid[best_index] = candidate
            if search():
                return True
        grid[best_index] = 0
        return False

    if not search():
        raise PuzzleValidationError("hrm.sudoku_unsatisfiable")
    return tuple(grid)


def generate_sudoku_record(seed: int, *, blanks: int = 8) -> dict[str, Any]:
    if not 1 <= blanks <= 48:
        raise ValueError("blanks must be between 1 and 48")
    randomizer = random.Random(seed)
    base = [((row * 3 + row // 3 + column) % 9) + 1 for row in range(9) for column in range(9)]
    digits = list(range(1, 10))
    randomizer.shuffle(digits)
    solution = [digits[value - 1] for value in base]
    blank_indexes = randomizer.sample(range(81), blanks)
    puzzle = list(solution)
    for index in blank_indexes:
        puzzle[index] = 0
    return {
        "puzzle_id": f"sudoku-seed-{seed}",
        "puzzle": [puzzle[offset : offset + 9] for offset in range(0, 81, 9)],
        "solution": [solution[offset : offset + 9] for offset in range(0, 81, 9)],
    }


class SudokuReferencePlugin:
    profile_id = "hrm-sudoku-reference-v1"
    puzzle_type = "sudoku"
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
                raise PuzzleValidationError("hrm.sudoku_record_invalid")
            puzzle, expected = validate_sudoku_record(raw_record)
            correct += int(solve_sudoku(puzzle) == expected)
        return {"metrics": exact_accuracy(correct, len(records)), "artifacts": []}


def _candidates(grid: list[int], index: int) -> set[int]:
    row, column = divmod(index, 9)
    used = set(grid[row * 9 : row * 9 + 9])
    used.update(grid[column::9])
    box_row = (row // 3) * 3
    box_column = (column // 3) * 3
    used.update(
        grid[(box_row + offset_row) * 9 + box_column + offset_column]
        for offset_row in range(3)
        for offset_column in range(3)
    )
    return set(_DIGITS).difference(used)


def _valid_partial_grid(grid: tuple[int, ...]) -> bool:
    groups = []
    groups.extend(grid[row * 9 : row * 9 + 9] for row in range(9))
    groups.extend(grid[column::9] for column in range(9))
    groups.extend(
        tuple(
            grid[(box_row + row) * 9 + box_column + column]
            for row in range(3)
            for column in range(3)
        )
        for box_row in (0, 3, 6)
        for box_column in (0, 3, 6)
    )
    return all(len(nonzero := [value for value in group if value]) == len(set(nonzero)) for group in groups)


def _valid_completed_grid(grid: tuple[int, ...]) -> bool:
    return _valid_partial_grid(grid) and all(value in _DIGITS for value in grid)


__all__ = [
    "SudokuReferencePlugin",
    "generate_sudoku_record",
    "normalize_sudoku_grid",
    "solve_sudoku",
    "validate_sudoku_record",
]
