"""Narrow TaskQueue adapter for fenced Native creation and normal notifications."""

from __future__ import annotations

import time

from agent.db_models import TaskDB
from agent.services.task_organization_scope import resolve_ingest_scope
from agent.services.task_runtime_service import append_task_history_event
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_status_service import normalize_task_status
from agent.services.workflow_runtime.lease_fencing import validate_sqlalchemy_lease
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


def native_command_bound_to_lease(task, lease) -> NativeNodeCommand:
    """The Native node command of ``task``, verified against the run-control lease it is delegated under."""
    context = task.worker_execution_context or {}
    command = NativeNodeCommand.from_mapping(context.get("native_node_command") or {})
    if (
        context.get("schema") != "ananta.native_graph_worker_context.v1"
        or context.get("runtime_path") != "native_graph_node"
        or task.tenant_id != command.tenant_id
        or task.task_kind != command.node.task_kind
        or set(task.required_capabilities or []) != set(command.node.required_capabilities)
        or task.derivation_reason != "native_graph_hub_delegation"
        or command.plan_hash != lease.checkpoint.plan_hash
        or command.policy_version != lease.checkpoint.policy_version
        or command.control_task_id != lease.checkpoint.state.business_data["control_task_id"]
    ):
        raise ValueError("bpmn_fenced_task_binding_mismatch")
    return command


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
    command = native_command_bound_to_lease(task, lease)
    _persisted, inserted = repository.insert_native_task_fenced(
        task,
        native_command=command.to_dict(),
        lease_guard=lambda session: validate_sqlalchemy_lease(session, lease, command),
    )
    if inserted:
        post_commit(task_id, old_status="todo", event_type=event_type)
