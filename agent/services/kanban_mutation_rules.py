"""Write-side rules of Kanban card commands.

Idempotency fingerprints, the audited task-history record, column ranking,
state-machine transition checks, dependency cycle checks and the mapping of
store conflicts to service errors. They operate on rows the caller already
holds inside its store transaction.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from agent.db_models.tasks import TaskDB
from agent.repositories.kanban_projection import (
    KanbanRevisionConflict,
    KanbanTaskNotFound,
)
from agent.services.hub_event_service import build_task_history_event
from agent.services.kanban_authorization_service import KanbanPrincipal
from agent.services.kanban_board_projection import (
    STATUS_ALIASES,
    kanban_column,
    kanban_sort_key,
)
from agent.services.kanban_service_error import KanbanServiceError
from agent.services.kanban_vector_task_boundary import is_kanban_rankable_task
from agent.services.task_state_machine_service import can_transition_to
from ananta_contracts.kanban import KanbanColumnId


@dataclass(frozen=True)
class KanbanMutation:
    key_hash: str
    digest: str


def kanban_mutation_fingerprint(
    principal: KanbanPrincipal,
    key: str,
    name: str,
    payload: dict[str, Any],
) -> KanbanMutation:
    key_hash = hashlib.sha256(f"{principal.subject}:{name}:{key}".encode()).hexdigest()
    raw = json.dumps({"key": key_hash, "payload": payload}, sort_keys=True, default=str)
    return KanbanMutation(key_hash, hashlib.sha256(raw.encode()).hexdigest())


def record_kanban_history_event(
    task: TaskDB,
    *,
    event_type: str,
    message: str,
    actor: str,
    mutation: KanbanMutation,
    details: dict[str, Any],
) -> None:
    task.kanban_revision = int(task.kanban_revision or 0) + 1
    task.updated_at = time.time()
    event = build_task_history_event(
        task,
        event_type,
        actor=actor,
        details={
            "actor_id": actor,
            "summary": message,
            "kanban_revision": task.kanban_revision,
            "idempotency_key_hash": mutation.key_hash,
            "idempotency_digest": mutation.digest,
            **details,
        },
    )
    task.history = [*list(task.history or []), event]


def rank_kanban_tasks(
    tasks: list[TaskDB],
    moved: TaskDB,
    target: KanbanColumnId,
    position: int,
    source: KanbanColumnId | None = None,
) -> None:
    source = source or kanban_column(moved.status)
    for column in {source, target}:
        values = [
            task
            for task in tasks
            if task.id != moved.id
            and is_kanban_rankable_task(task)
            and kanban_column(task.status) == column
        ]
        values.sort(key=kanban_sort_key)
        if column == target:
            values.insert(min(position, len(values)), moved)
        for index, task in enumerate(values):
            rank = (index + 1) * 1024
            if int(task.kanban_position or 0) != rank:
                task.kanban_position = rank
                if task.id != moved.id:
                    task.kanban_revision = int(task.kanban_revision or 0) + 1
                    task.updated_at = time.time()


def require_kanban_transition(task: TaskDB, target: str) -> None:
    current = STATUS_ALIASES.get(str(task.status), str(task.status))
    allowed = can_transition_to(current, target)
    if isinstance(allowed, tuple):
        allowed = allowed[0]
    if not allowed:
        raise KanbanServiceError(
            "kanban_transition_invalid",
            f"task cannot transition from {current} to {target}",
            status_code=409,
        )


def require_acyclic_kanban_dependencies(target: TaskDB, dependencies: tuple[str, ...], tasks: list[TaskDB]) -> None:
    dependencies = tuple(dict.fromkeys(dependencies))
    by_id = {task.id: task for task in tasks}
    if target.id in dependencies:
        raise KanbanServiceError(
            "kanban_dependency_cycle", "a card cannot depend on itself", status_code=409
        )
    missing = [value for value in dependencies if value not in by_id]
    if missing:
        raise KanbanServiceError(
            "kanban_dependency_not_found",
            "dependencies must belong to the same board",
            status_code=404,
            details={"missing": missing},
        )
    graph = {
        task.id: list(dependencies if task.id == target.id else (task.depends_on or []))
        for task in tasks
    }

    def reaches(node: str, visited: set[str]) -> bool:
        if node == target.id:
            return True
        if node in visited:
            return False
        visited.add(node)
        return any(reaches(child, visited) for child in graph.get(node, []))

    if any(reaches(value, set()) for value in dependencies):
        raise KanbanServiceError(
            "kanban_dependency_cycle", "dependencies would create a cycle", status_code=409
        )


def kanban_store_error(exc: Exception) -> KanbanServiceError:
    if isinstance(exc, KanbanTaskNotFound):
        return KanbanServiceError("kanban_card_not_found", "card was not found", status_code=404)
    if isinstance(exc, KanbanRevisionConflict):
        return KanbanServiceError(
            "kanban_revision_conflict",
            "the card was changed by another command",
            status_code=409,
            details={"current_revision": exc.current_revision},
        )
    return KanbanServiceError("kanban_idempotency_conflict", str(exc), status_code=409)
