"""Read-side projection of hub tasks onto Kanban boards, cards and pages.

Pure functions: status-to-column mapping, stable ordering and ranking keys,
board revisions, card/board models and revision-bound paging cursors. They
never read the store or authorize; ``KanbanProjectionService`` does both.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from agent.db_models.tasks import TaskDB
from agent.repositories.kanban_projection import KanbanScope
from agent.services.kanban_service_error import KanbanServiceError
from ananta_contracts.kanban import (
    KanbanAssignee,
    KanbanBoard,
    KanbanCapability,
    KanbanCard,
    KanbanColumn,
    KanbanColumnId,
    KanbanScopeType,
)

STATUS_ALIASES = {
    "backlog": "todo",
    "created": "todo",
    "in-progress": "in_progress",
    "done": "completed",
    "blocked": "blocked_by_dependency",
}
COLUMN_STATUSES = {
    KanbanColumnId.TODO: ("todo", "created", "assigned", "proposing", "updated"),
    KanbanColumnId.IN_PROGRESS: ("in_progress", "delegated", "waiting_for_review", "paused"),
    KanbanColumnId.BLOCKED: (
        "blocked_by_dependency",
        "blocked",
        "failed",
        "cancelled",
        "verification_failed",
    ),
    KanbanColumnId.COMPLETED: ("completed", "done", "skipped"),
}
COLUMN_TARGET = {
    KanbanColumnId.TODO: "todo",
    KanbanColumnId.IN_PROGRESS: "in_progress",
    KanbanColumnId.BLOCKED: "blocked_by_dependency",
    KanbanColumnId.COMPLETED: "completed",
}
COLUMN_TITLE = {
    KanbanColumnId.TODO: "To do",
    KanbanColumnId.IN_PROGRESS: "In progress",
    KanbanColumnId.BLOCKED: "Blocked",
    KanbanColumnId.COMPLETED: "Completed",
}
COLUMN_ORDER = tuple(KanbanColumnId)


def kanban_column(status: str | None) -> KanbanColumnId:
    normalized = STATUS_ALIASES.get(str(status or "todo").lower(), str(status or "todo").lower())
    for column, statuses in COLUMN_STATUSES.items():
        if normalized in statuses:
            return column
    return KanbanColumnId.BLOCKED


def kanban_sort_key(task: TaskDB) -> tuple[int, str, str]:
    position = int(task.kanban_position or 0)
    created = task.created_at.isoformat() if isinstance(task.created_at, datetime) else ""
    return (0, f"{position:020d}", task.id) if position > 0 else (1, created, task.id)


def ordered_kanban_tasks(tasks: Iterable[TaskDB]) -> list[TaskDB]:
    grouped = {column: [] for column in COLUMN_ORDER}
    for task in tasks:
        grouped[kanban_column(task.status)].append(task)
    return [
        task
        for column in COLUMN_ORDER
        for task in sorted(grouped[column], key=kanban_sort_key)
    ]


def kanban_board_revision(scope: KanbanScope, tasks: Iterable[TaskDB]) -> str:
    values = [
        (
            task.id,
            str(task.status),
            int(task.kanban_position or 0),
            int(task.kanban_revision or 0),
            task.updated_at.isoformat() if isinstance(task.updated_at, datetime) else "",
        )
        for task in sorted(tasks, key=lambda item: item.id)
    ]
    raw = json.dumps({"board": scope.board_id, "tasks": values}, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def kanban_history_event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or event.get("event") or "")


def kanban_card_assignee(task: TaskDB) -> KanbanAssignee | None:
    context = task.worker_execution_context if isinstance(task.worker_execution_context, dict) else {}
    assignee_id = context.get("kanban_assignee_id")
    if not assignee_id and not task.assigned_agent_url:
        return None
    return KanbanAssignee(
        id=str(assignee_id or task.assigned_agent_url),
        name=context.get("kanban_assignee_name"),
        url=task.assigned_agent_url,
    )


def project_kanban_cards(scope: KanbanScope, tasks: list[TaskDB]) -> list[KanbanCard]:
    by_id = {task.id: task for task in tasks}
    positions = {column: 0 for column in COLUMN_ORDER}
    result = []
    for task in ordered_kanban_tasks(tasks):
        column = kanban_column(task.status)
        history = [event for event in list(task.history or []) if isinstance(event, dict)]
        dependencies = tuple(str(value) for value in list(task.depends_on or []))
        blocked = column == KanbanColumnId.BLOCKED or any(
            value not in by_id or kanban_column(by_id[value].status) != KanbanColumnId.COMPLETED
            for value in dependencies
        )
        context = task.worker_execution_context if isinstance(task.worker_execution_context, dict) else {}
        labels = context.get("kanban_labels")
        result.append(
            KanbanCard(
                id=task.id,
                board_id=scope.board_id,
                title=task.title or "",
                description=task.description,
                status=str(task.status),
                column_id=column,
                position=positions[column],
                revision=int(task.kanban_revision or 0),
                priority=str(task.priority or "Medium"),
                assignee=kanban_card_assignee(task),
                labels=tuple(labels) if isinstance(labels, list) else (),
                blocked=blocked,
                dependencies=dependencies,
                comment_count=sum(kanban_history_event_type(event) == "kanban_comment_added" for event in history),
                activity_count=sum(kanban_history_event_type(event).startswith("kanban_") for event in history),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )
        positions[column] += 1
    return result


def project_kanban_board(
    scope: KanbanScope,
    tasks: list[TaskDB],
    *,
    capabilities: tuple[KanbanCapability, ...],
    goal: Any | None = None,
    team: Any | None = None,
) -> KanbanBoard:
    cards = project_kanban_cards(scope, tasks)
    counts = {column: 0 for column in COLUMN_ORDER}
    for card in cards:
        counts[card.column_id] += 1
    name = (
        "Hub task board"
        if scope.kind == "hub"
        else str(getattr(goal, "goal", None) or f"Goal {scope.scope_id}")
        if scope.kind == "goal"
        else str(getattr(team, "name", None) or f"Team {scope.scope_id}")
    )
    return KanbanBoard(
        id=scope.board_id,
        name=name,
        scope_type=KanbanScopeType(scope.kind),
        scope_id=scope.scope_id,
        revision=kanban_board_revision(scope, tasks),
        card_count=len(cards),
        capabilities=capabilities,
        columns=tuple(
            KanbanColumn(
                id=column,
                title=COLUMN_TITLE[column],
                statuses=COLUMN_STATUSES[column],
                card_count=counts[column],
            )
            for column in COLUMN_ORDER
        ),
    )


def encode_kanban_cursor(offset: int, revision: str) -> str:
    raw = json.dumps({"offset": offset, "revision": revision}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_kanban_cursor_offset(cursor: str | None, revision: str) -> int:
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if payload.get("revision") != revision:
            raise KanbanServiceError(
                "kanban_cursor_stale", "the board changed while paging", status_code=409
            )
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError
        return offset
    except KanbanServiceError:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise KanbanServiceError("kanban_cursor_invalid", "cursor is invalid", status_code=400) from exc


def require_kanban_page_limit(limit: int) -> None:
    if not 1 <= limit <= 200:
        raise KanbanServiceError(
            "kanban_limit_invalid", "limit must be between 1 and 200", status_code=400
        )
