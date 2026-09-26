"""Narrow TaskQueue adapter for fenced Native creation and normal notifications."""

from __future__ import annotations

import time

from agent.db_models import TaskDB
from agent.services.task_organization_scope import resolve_ingest_scope
from agent.services.task_runtime_service import append_task_history_event
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_status_service import normalize_task_status


def ingest_native_task_fenced(
    *,
    repository,
    source_resolver,
    post_commit,
    lease,
    task_id,
    status,
    title=None,
    description=None,
    priority="medium",
    created_by="unknown",
    source="ui",
    team_id=None,
    tags=None,
    event_type="task_ingested",
    event_channel="central_task_management",
    event_details=None,
    extra_fields=None,
):
    """Preserve ingestion's scope, status, history and post-commit contracts."""
    fields = dict(extra_fields or {})
    scope, resolved_team = resolve_ingest_scope(source_resolver(fields), fields, team_id)
    fields.update(scope)
    if {"id", "status", "history", "created_at", "updated_at"}.intersection(fields):
        raise ValueError("bpmn_fenced_task_reserved_field")
    normalized = normalize_task_status(status, default="todo")
    allowed, _reason = can_transition_to("todo", normalized)
    if not allowed and normalized != "todo":
        raise ValueError("bpmn_fenced_task_initial_status_invalid")
    if normalized not in {"todo", "created"}:
        raise ValueError("bpmn_fenced_task_initial_status_invalid")
    timestamp = time.time()
    task = TaskDB(
        id=task_id,
        created_at=timestamp,
        updated_at=timestamp,
        status=normalized,
        title=str(title or "")[:200] or None,
        description=description,
        priority=priority,
        team_id=resolved_team,
        tags=list(tags or []),
        **fields,
    )
    details = {"source": source, "channel": event_channel, "tags": list(tags or [])}
    if isinstance(event_details, dict):
        details.update(event_details)
    append_task_history_event(task, event_type=event_type, actor=created_by or "unknown", details=details)
    _persisted, inserted = repository.insert_native_task_fenced(task, lease=lease)
    if inserted:
        post_commit(task_id, old_status="todo", event_type=event_type)
