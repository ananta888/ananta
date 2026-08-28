"""Worker dispatcher for exactly one Hub-assigned expert task attempt."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from ananta_contracts.knowledge_expert_task import KnowledgeExpertTask
from ananta_contracts.parametric_knowledge import canonical_sha256


class KnowledgeExpertJobPort(Protocol):
    def execute(self, task: KnowledgeExpertTask, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class KnowledgeExpertJobHandler:
    def __init__(self, *, worker_audience: str, handlers: Mapping[str, KnowledgeExpertJobPort]) -> None:
        self._worker_audience = worker_audience
        self._handlers = dict(handlers)

    def execute(
        self,
        task: KnowledgeExpertTask,
        *,
        current_attempt_id: str,
        payload: Mapping[str, Any],
        payload_digest: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        task.assert_executable(
            worker_audience=self._worker_audience,
            current_attempt_id=current_attempt_id,
            now=now,
        )
        if (
            payload_digest != task.input_digest
            or canonical_sha256(payload) != payload_digest
        ):
            raise ValueError("knowledge_expert_task_input_digest_mismatch")
        handler = self._handlers.get(task.task_type)
        if handler is None:
            raise ValueError("knowledge_expert_task_handler_unavailable")
        result = dict(handler.execute(task, payload))
        if any(key in result for key in ("activate", "active_generation", "next_task")):
            raise ValueError("knowledge_expert_worker_orchestration_denied")
        return {
            "schema": "ananta.knowledge-expert-task-result.v1",
            "task_id": task.task_id,
            "attempt_id": task.attempt_id,
            "task_type": task.task_type,
            "activation_authorized": False,
            "result": result,
        }


__all__ = ["KnowledgeExpertJobHandler", "KnowledgeExpertJobPort"]
