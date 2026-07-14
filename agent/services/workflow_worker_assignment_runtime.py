"""Production composition for Hub-controlled workflow Worker assignments."""

from __future__ import annotations

import threading
from typing import Any, Mapping

from flask import current_app

from agent.services.workflow_runtime import SQLAlchemyExecutionOwnershipStore
from agent.services.workflow_worker_assignment_service import (
    SQLAlchemyWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
    WorkflowWorkerAssignmentService,
    WorkflowWorkerAssignmentStore,
)
from agent.services.workflow_worker_service_auth import (
    registered_worker_auth_required,
)

_STORE: WorkflowWorkerAssignmentStore | None = None
_SERVICE: WorkflowWorkerAssignmentService | None = None
_LOCK = threading.RLock()


def get_workflow_worker_assignment_store() -> WorkflowWorkerAssignmentStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    with _LOCK:
        if _STORE is None:
            from agent.database import engine

            _STORE = SQLAlchemyWorkflowWorkerAssignmentStore(engine)
    return _STORE


def get_workflow_worker_assignment_service() -> WorkflowWorkerAssignmentService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _LOCK:
        if _SERVICE is None:
            from agent.database import engine

            _SERVICE = WorkflowWorkerAssignmentService(
                ownership=SQLAlchemyExecutionOwnershipStore(engine),
                assignments=get_workflow_worker_assignment_store(),
            )
    return _SERVICE


def bind_dispatched_workflow_task(
    *,
    task: Any,
    worker: Any,
    config: Mapping[str, Any] | None = None,
) -> WorkflowWorkerAssignment | None:
    source = config if config is not None else current_app.config
    if not registered_worker_auth_required(source):
        return None
    registered_worker = _resolve_registered_worker(worker)
    return get_workflow_worker_assignment_service().bind_dispatched_task(
        task=task,
        worker=registered_worker,
    )


def _resolve_registered_worker(worker: Any) -> Any:
    """Reload the selected Worker from the Hub-owned registry.

    Dispatch projections and synthetic local fallbacks must never become an
    authority for registration provenance or capabilities.
    """

    from agent.services.repository_registry import get_repository_registry

    worker_url = str(_value(worker, "url") or "").strip()
    selected_name = str(_value(worker, "name") or "").strip()
    registered = (
        get_repository_registry().agent_repo.get_by_url(worker_url)
        if worker_url
        else None
    )
    if registered is None or (
        selected_name
        and selected_name != str(getattr(registered, "name", "") or "").strip()
    ):
        from agent.services.workflow_worker_assignment_service import (
            WorkflowWorkerAssignmentError,
        )

        raise WorkflowWorkerAssignmentError(
            "workflow_worker_assignment_registry_identity_denied",
            status_code=403,
        )
    return registered


def _value(value: object, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def reset_workflow_worker_assignment_runtime() -> None:
    global _SERVICE, _STORE
    with _LOCK:
        _SERVICE = None
        _STORE = None


__all__ = [
    "bind_dispatched_workflow_task",
    "get_workflow_worker_assignment_service",
    "get_workflow_worker_assignment_store",
    "reset_workflow_worker_assignment_runtime",
]
