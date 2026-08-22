"""Bounded reference puzzle plugins for HRM experiment baselines."""

from worker.hrm_experiments.puzzles.arc import ArcReferencePlugin
from worker.hrm_experiments.puzzles.maze import MazeReferencePlugin
from worker.hrm_experiments.puzzles.sudoku import SudokuReferencePlugin

__all__ = ["ArcReferencePlugin", "MazeReferencePlugin", "SudokuReferencePlugin"]
