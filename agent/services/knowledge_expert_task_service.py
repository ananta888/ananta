"""Hub-owned creation and cancellation checks for knowledge-expert tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ananta_contracts.knowledge_expert_task import KnowledgeExpertTask


class KnowledgeExpertTaskQueuePort(Protocol):
    def enqueue(self, task: KnowledgeExpertTask) -> None: ...

    def is_cancelled(self, *, task_id: str, attempt_id: str) -> bool: ...


class KnowledgeExpertTaskService:
    """The Hub owns task identity and queueing; Workers receive one attempt."""

    def __init__(self, *, queue: KnowledgeExpertTaskQueuePort, clock: Callable[[], datetime] | None = None) -> None:
        self._queue = queue
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def submit(
        self,
        *,
        task_id: str,
        attempt_id: str,
        task_type: str,
        worker_audience: str,
        tenant_id: str,
        workspace_id: str,
        repository_id: str,
        input_digest: str,
        policy_digest: str,
        ttl_seconds: int = 3600,
    ) -> KnowledgeExpertTask:
        if not 1 <= ttl_seconds <= 86_400:
            raise ValueError("knowledge_expert_task_ttl_invalid")
        current = self._clock()
        if current.tzinfo is None:
            raise ValueError("knowledge_expert_task_clock_invalid")
        deadline = current + timedelta(seconds=ttl_seconds)
        task = KnowledgeExpertTask.from_mapping(
            {
                "schema": "ananta.knowledge-expert-task.v1",
                "task_id": task_id,
                "attempt_id": attempt_id,
                "task_type": task_type,
                "worker_audience": worker_audience,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "repository_id": repository_id,
                "input_digest": input_digest,
                "policy_digest": policy_digest,
                "deadline": deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )
        self._queue.enqueue(task)
        return task

    def assert_result_current(self, task: KnowledgeExpertTask, *, attempt_id: str) -> None:
        if attempt_id != task.attempt_id:
            raise ValueError("knowledge_expert_task_stale_result")
        if self._queue.is_cancelled(task_id=task.task_id, attempt_id=attempt_id):
            raise ValueError("knowledge_expert_task_cancelled")


__all__ = ["KnowledgeExpertTaskQueuePort", "KnowledgeExpertTaskService"]
