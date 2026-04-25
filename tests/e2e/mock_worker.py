from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerExecution:
    task_id: str
    status: str
    output: str
    artifact_body: str


class MockWorker:
    """Deterministic worker fixture for E2E dogfood tests."""

    def execute(self, *, task_id: str, prompt: str) -> WorkerExecution:
        return WorkerExecution(
            task_id=task_id,
            status="completed",
            output=f"worker completed {task_id}",
            artifact_body="\n".join(
                [
                    f"task_id={task_id}",
                    f"prompt={prompt}",
                    "status=completed",
                    "artifact_kind=mock_result",
                ]
            ),
        )
