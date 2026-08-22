from __future__ import annotations

from worker.hrm_experiments.puzzles.arc import ArcReferencePlugin, infer_arc_output
from worker.hrm_experiments.puzzles.maze import (
    MazeReferencePlugin,
    generate_maze_record,
    normalize_maze,
    solve_maze,
)
from worker.hrm_experiments.puzzles.sudoku import (
    SudokuReferencePlugin,
    generate_sudoku_record,
    solve_sudoku,
    validate_sudoku_record,
)


def test_sudoku_generator_and_solver_are_seed_reproducible():
    first = generate_sudoku_record(11)
    second = generate_sudoku_record(11)
    puzzle, solution = validate_sudoku_record(first)

    assert first == second
    assert solve_sudoku(puzzle) == solution
    metrics = SudokuReferencePlugin().execute({}, {"records": [first]})["metrics"]
    assert metrics[1]["value"] == 1.0


def test_maze_generator_and_solver_are_bounded_and_reproducible():
    first = generate_maze_record(17)
    second = generate_maze_record(17)

    assert first == second
    assert solve_maze(normalize_maze(first["grid"])) == tuple(
        tuple(point) for point in first["solution"]
    )
    metrics = MazeReferencePlugin().execute({}, {"records": [first]})["metrics"]
    assert metrics[1]["value"] == 1.0


def test_arc_reference_profile_learns_rotation_and_color_mapping():
    rotation_record = {
        "puzzle_id": "arc-rotate",
        "train": [
            {"input": [[1, 2], [3, 4]], "output": [[3, 1], [4, 2]]}
        ],
        "test": {
            "input": [[5, 6], [7, 8]],
            "output": [[7, 5], [8, 6]],
        },
    }
    color_training = (
        (((1, 0), (0, 1)), ((2, 0), (0, 2))),
    )

    assert infer_arc_output(color_training, ((1, 1),)) == ((2, 2),)
    metrics = ArcReferencePlugin().execute(
        {}, {"records": [rotation_record]}
    )["metrics"]
    assert metrics[1]["value"] == 1.0
