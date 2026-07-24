from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class KanbanCardSlot:
    task: Mapping[str, Any]
    flat_index: int
    column_id: str
    column_title: str
    column_index: int
    source_row: int


@dataclass(frozen=True, slots=True)
class KanbanColumnWindow:
    id: str
    title: str
    column_index: int
    row_start: int
    slots: tuple[KanbanCardSlot, ...]


@dataclass(frozen=True, slots=True)
class KanbanViewportPlan:
    layout: Literal["list", "columns"]
    card_row_capacity: int
    list_slots: tuple[KanbanCardSlot, ...] = ()
    columns: tuple[KanbanColumnWindow, ...] = ()


def _tasks(column: Mapping[str, Any]) -> Sequence[object]:
    value = column.get("tasks")
    if isinstance(value, (list, tuple)):
        return value
    return ()


def _flat_index(task: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(task.get("_flat_index", fallback))
    except (TypeError, ValueError):
        return fallback


def _page_start(*, selected: int, capacity: int, total: int) -> int:
    if capacity <= 0 or total <= capacity:
        return 0
    clamped = max(0, min(total - 1, int(selected)))
    page_start = (clamped // capacity) * capacity
    return min(page_start, total - capacity)


def _slot(
    *,
    task: Mapping[str, Any],
    fallback_index: int,
    column: Mapping[str, Any],
    column_index: int,
    source_row: int,
) -> KanbanCardSlot:
    column_id = str(column.get("id") or f"column-{column_index}")
    return KanbanCardSlot(
        task=task,
        flat_index=_flat_index(task, fallback_index),
        column_id=column_id,
        column_title=str(column.get("title") or column_id),
        column_index=column_index,
        source_row=source_row,
    )


def plan_kanban_viewport(
    payload: Mapping[str, Any],
    *,
    width: int,
    height: int,
    selected_index: int,
    max_columns: int = 4,
) -> KanbanViewportPlan:
    """Project a Kanban payload into the visible, selection-aware viewport."""
    raw_columns = payload.get("columns")
    columns = (
        tuple(column for column in raw_columns if isinstance(column, Mapping))
        if isinstance(raw_columns, (list, tuple))
        else ()
    )
    indexed_columns: list[
        tuple[int, Mapping[str, Any], Sequence[object], int]
    ] = []
    total = 0
    for column_index, column in enumerate(columns):
        tasks = _tasks(column)
        indexed_columns.append((column_index, column, tasks, total))
        total += len(tasks)

    selected = max(0, min(max(0, total - 1), int(selected_index)))
    layout: Literal["list", "columns"] = (
        "columns" if max(12, int(width)) >= 100 else "list"
    )

    if layout == "list":
        capacity = max(0, int(height) - 1)
        start = _page_start(
            selected=selected,
            capacity=capacity,
            total=total,
        )
        stop = min(total, start + capacity)
        slots: list[KanbanCardSlot] = []
        for column_index, column, tasks, offset in indexed_columns:
            local_start = max(0, start - offset)
            local_stop = min(len(tasks), stop - offset)
            if local_start >= local_stop:
                continue
            for source_row in range(local_start, local_stop):
                task = tasks[source_row]
                if not isinstance(task, Mapping):
                    continue
                slots.append(
                    _slot(
                        task=task,
                        fallback_index=offset + source_row,
                        column=column,
                        column_index=column_index,
                        source_row=source_row,
                    )
                )
        return KanbanViewportPlan(
            layout="list",
            card_row_capacity=capacity,
            list_slots=tuple(slots),
        )

    capacity = max(0, int(height) - 2)
    selected_column = 0
    selected_row = 0
    for column_index, _column, tasks, offset in indexed_columns:
        if tasks and offset <= selected < offset + len(tasks):
            selected_column = column_index
            selected_row = selected - offset
            break

    safe_max_columns = max(1, int(max_columns))
    column_start = (selected_column // safe_max_columns) * safe_max_columns
    visible = indexed_columns[column_start : column_start + safe_max_columns]
    maximum_rows = max((len(tasks) for _, _, tasks, _ in visible), default=0)
    row_start = _page_start(
        selected=selected_row,
        capacity=capacity,
        total=maximum_rows,
    )
    planned_columns: list[KanbanColumnWindow] = []
    for column_index, column, tasks, offset in visible:
        slots: list[KanbanCardSlot] = []
        for source_row in range(row_start, min(len(tasks), row_start + capacity)):
            task = tasks[source_row]
            if not isinstance(task, Mapping):
                continue
            slots.append(
                _slot(
                    task=task,
                    fallback_index=offset + source_row,
                    column=column,
                    column_index=column_index,
                    source_row=source_row,
                )
            )
        column_id = str(column.get("id") or f"column-{column_index}")
        planned_columns.append(
            KanbanColumnWindow(
                id=column_id,
                title=str(column.get("title") or column_id),
                column_index=column_index,
                row_start=row_start,
                slots=tuple(slots),
            )
        )
    return KanbanViewportPlan(
        layout="columns",
        card_row_capacity=capacity,
        columns=tuple(planned_columns),
    )
