"""Atomic birth of one delegated Task under its Hub run-control lease."""

from __future__ import annotations

from sqlmodel import select

from agent.db_models import TaskDB
from agent.repositories.task_repository_session import task_repository_session
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.lease_fencing import validate_sqlalchemy_lease
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


def insert_native_task_fenced(*, engine, task, lease, prepare_new):
    """No independent queue or commit: lease authority and Task share one DB.

    An existing task is read-only and accepted only for the exact immutable
    delegation. No task status, grant or assignment is reconstructed here.
    """
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
    with task_repository_session(engine, write=True) as session:
        validate_sqlalchemy_lease(session, lease, command)
        statement = select(TaskDB).where(TaskDB.id == task.id)
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update()
        existing = session.exec(statement).one_or_none()
        if existing is not None:
            stored = (existing.worker_execution_context or {}).get("native_node_command")
            if existing.tenant_id != task.tenant_id or canonical_json(stored) != canonical_json(command.to_dict()):
                raise ValueError("native_hub_task_id_conflict")
            return existing, False
        original_context = canonical_json(task.worker_execution_context)
        prepared = prepare_new(task, session=session)
        if canonical_json(prepared.worker_execution_context) != original_context:
            raise ValueError("bpmn_fenced_task_policy_binding_changed")
        session.add(prepared)
        session.flush()
        # The anchor lock remains held through this commit; a successor's lease
        # cannot become current between validation and Task visibility.
        session.commit()
        session.refresh(prepared)
        return prepared, True
