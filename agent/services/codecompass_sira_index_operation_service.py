"""Hub-owned submission and observation of SIRA index operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.sira_index_operation import (
    CONTEXT_KEY,
    TASK_KIND,
    SiraIndexOperation,
)


class SiraIndexOperationTaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class SiraIndexOperationQueuePort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class SiraIndexOperationConflict(ValueError):
    pass


class CodeCompassSiraIndexOperationService:
    """Create immutable, idempotent work in the Hub's central task queue."""

    def __init__(
        self,
        *,
        task_repository: SiraIndexOperationTaskRepositoryPort,
        task_queue: SiraIndexOperationQueuePort,
    ) -> None:
        self._tasks = task_repository
        self._queue = task_queue

    def submit(
        self,
        *,
        operation: str,
        tenant_id: str,
        project_id: str,
        repository_id: str,
        snapshot_artifact_id: str,
        idempotency_key: str,
        actor_id: str,
    ) -> dict[str, Any]:
        command = SiraIndexOperation.create(
            operation=operation,
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repository_id,
            snapshot_artifact_id=snapshot_artifact_id,
            idempotency_key=idempotency_key,
        )
        existing = self._tasks.get_by_id(command.operation_id)
        if existing is not None:
            persisted = self._mapping(existing)
            context = persisted.get("worker_execution_context")
            bound = context.get(CONTEXT_KEY) if isinstance(context, Mapping) else None
            if bound != command.to_dict():
                raise SiraIndexOperationConflict("sira_index_operation_idempotency_conflict")
            return self._projection(persisted, replayed=True)
        self._queue.ingest_task(
            task_id=command.operation_id,
            status="todo",
            title=f"SIRA {command.operation}: {command.repository_id}",
            description="Hub-delegated CodeCompass SIRA index operation",
            priority="high",
            created_by=str(actor_id or "system")[:191],
            source="codecompass_sira",
            event_channel="codecompass_sira_index_operations",
            event_details={
                "operation": command.operation,
                "repository_id": command.repository_id,
                "request_digest": command.request_digest,
            },
            extra_fields={
                "tenant_id": command.tenant_id,
                "project_id": command.project_id,
                "task_kind": TASK_KIND,
                "required_capabilities": ["retrieval", "index_write", "sira_index"],
                "worker_execution_context": {CONTEXT_KEY: command.to_dict()},
            },
        )
        created = self._tasks.get_by_id(command.operation_id)
        payload = (
            self._mapping(created)
            if created is not None
            else {
                "id": command.operation_id,
                "status": "todo",
                "tenant_id": command.tenant_id,
                "project_id": command.project_id,
                "worker_execution_context": {CONTEXT_KEY: command.to_dict()},
            }
        )
        return self._projection(payload, replayed=False)

    def get(
        self,
        operation_id: str,
        *,
        tenant_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        task = self._tasks.get_by_id(str(operation_id or "").strip())
        if task is None:
            return None
        payload = self._mapping(task)
        if str(payload.get("tenant_id") or "") != str(tenant_id or "") or str(payload.get("project_id") or "") != str(
            project_id or ""
        ):
            return None
        context = payload.get("worker_execution_context")
        bound = context.get(CONTEXT_KEY) if isinstance(context, Mapping) else None
        if not isinstance(bound, Mapping):
            return None
        SiraIndexOperation.from_mapping(bound)
        return self._projection(payload, replayed=False)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        dump = getattr(value, "model_dump", None)
        return dict(dump()) if callable(dump) else {}

    @staticmethod
    def _projection(task: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        context = task.get("worker_execution_context")
        command = context.get(CONTEXT_KEY) if isinstance(context, Mapping) else {}
        return {
            "schema": "ananta.sira-index-operation-status.v1",
            "operation_id": str(task.get("id") or command.get("operation_id") or ""),
            "operation": str(command.get("operation") or ""),
            "repository_id": str(command.get("repository_id") or ""),
            "status": str(task.get("status") or "todo"),
            "status_reason_code": str(task.get("status_reason_code") or "") or None,
            "replayed": replayed,
        }


def get_codecompass_sira_index_operation_service() -> CodeCompassSiraIndexOperationService:
    from flask import current_app

    configured = current_app.extensions.get("codecompass_sira_index_operation_service")
    if configured is not None and all(callable(getattr(configured, method, None)) for method in ("submit", "get")):
        return configured
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_queue_service import get_task_queue_service

    service = CodeCompassSiraIndexOperationService(
        task_repository=get_repository_registry().task_repo,
        task_queue=get_task_queue_service(),
    )
    current_app.extensions["codecompass_sira_index_operation_service"] = service
    return service


__all__ = [
    "CodeCompassSiraIndexOperationService",
    "SiraIndexOperationConflict",
    "get_codecompass_sira_index_operation_service",
]
