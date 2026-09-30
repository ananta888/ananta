"""Optimistic-concurrency conflict raised by the ML-Intern training repositories."""

from __future__ import annotations


class MlInternTrainingRepositoryConflict(RuntimeError):
    pass


__all__ = ["MlInternTrainingRepositoryConflict"]
