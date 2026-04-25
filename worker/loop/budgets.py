from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerLoopBudgets:
    max_iterations: int = 3
    max_patch_attempts: int = 3
    max_runtime_seconds: int = 300

    def validate(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError("max_iterations_must_be_positive")
        if self.max_patch_attempts <= 0:
            raise ValueError("max_patch_attempts_must_be_positive")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds_must_be_positive")
