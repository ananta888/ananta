"""Fresh-process reproduction of concrete counterexamples."""

from __future__ import annotations

from pathlib import Path

from ananta_contracts.verification import VerificationAssignmentV1, VerificationStatus
from worker.verification.process_runner import VerificationProcessRunner


class CounterexampleReproducer:
    def __init__(self, process_runner: VerificationProcessRunner | None = None) -> None:
        self._runner = process_runner or VerificationProcessRunner()

    def reproduce(
        self,
        assignment: VerificationAssignmentV1,
        *,
        repository: Path,
        command: tuple[str, ...],
    ) -> tuple[VerificationStatus, str]:
        observation = self._runner.run(
            command,
            repository=repository,
            timeout_seconds=assignment.budgets.timeout_seconds,
            max_output_bytes=assignment.budgets.max_output_bytes,
            memory_mb=assignment.budgets.memory_mb,
        )
        if observation.timed_out:
            return VerificationStatus.TIMED_OUT, "reproduction_timeout"
        if observation.returncode == 0:
            return VerificationStatus.FAILED_TO_REPRODUCE, "counterexample_not_reproduced"
        return VerificationStatus.COUNTEREXAMPLE_FOUND, "counterexample_reproduced"


__all__ = ["CounterexampleReproducer"]
