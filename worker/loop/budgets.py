from __future__ import annotations

from dataclasses import dataclass

from worker.core.execution_profile import loop_budgets_for_profile, normalize_execution_profile


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


def budgets_for_profile(profile: str | None) -> WorkerLoopBudgets:
    normalized = normalize_execution_profile(profile)
    values = loop_budgets_for_profile(normalized)
    return WorkerLoopBudgets(
        max_iterations=int(values["max_iterations"]),
        max_patch_attempts=int(values["max_patch_attempts"]),
        max_runtime_seconds=int(values["max_runtime_seconds"]),
    )
