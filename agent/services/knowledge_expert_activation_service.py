"""Hub-only activation after a current, non-cancelled task result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent.services.knowledge_expert_task_service import KnowledgeExpertTaskService
from ananta_contracts.knowledge_expert_task import KnowledgeExpertTask


class KnowledgeExpertBankActivationPort(Protocol):
    def activate(
        self,
        *,
        bank_id: str,
        generation_id: str,
        expected_active_generation: str,
    ) -> Mapping[str, str]: ...


class KnowledgeExpertActivationService:
    def __init__(self, *, tasks: KnowledgeExpertTaskService, registry: KnowledgeExpertBankActivationPort) -> None:
        self._tasks = tasks
        self._registry = registry

    def activate_published_bank(
        self,
        *,
        task: KnowledgeExpertTask,
        result_attempt_id: str,
        bank_id: str,
        generation_id: str,
        expected_active_generation: str,
    ) -> Mapping[str, str]:
        if task.task_type != "publish_bank":
            raise ValueError("knowledge_expert_activation_task_type_denied")
        self._tasks.assert_result_current(task, attempt_id=result_attempt_id)
        return self._registry.activate(
            bank_id=bank_id,
            generation_id=generation_id,
            expected_active_generation=expected_active_generation,
        )


__all__ = ["KnowledgeExpertActivationService", "KnowledgeExpertBankActivationPort"]
