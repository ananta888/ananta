"""Hub-owned preauthorization for repeating an execution whose outcome is unknown."""

from collections.abc import Mapping

from flask import current_app, has_app_context
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.tasks import TaskDB

_FIELDS = frozenset({"purpose", "tenant_id", "workflow_id", "run_id", "plan_hash", "policy_version",
                     "step_id", "actor_id", "dispatch_binding_digest", "maximum_attempts"})


def authorize_uncertain_native_retry(command, *, plan, state, engine, policy_bindings):
    context = TaskDB.worker_execution_context["native_node_command"]
    statement = select(NativeDispatchDB, TaskDB).join(TaskDB, TaskDB.id == NativeDispatchDB.task_id).where(
        NativeDispatchDB.tenant_id == plan.tenant_id, TaskDB.tenant_id == plan.tenant_id,
        NativeDispatchDB.state.in_(("sending", "unknown")), context["run_id"].as_string() == command.run_id,
    )
    with Session(engine) as session:
        rows = list(session.execute(statement.limit(101)))
    if len(rows) > 100:
        return False, "native_retry_uncertain_dispatch_limit"
    if not rows:
        return True, "native_retry_no_uncertain_dispatch"
    if not isinstance(policy_bindings, list) or len(policy_bindings) > 100:
        return False, "native_retry_uncertain_dispatch_policy_required"
    for journal, task in rows:
        node_id = task.worker_execution_context["native_node_command"]["node"]["node_id"]
        expected = {
            "purpose": "native.retry.unknown_dispatch", "tenant_id": plan.tenant_id,
            "workflow_id": plan.workflow_id, "run_id": command.run_id, "plan_hash": plan.plan_hash,
            "policy_version": plan.policy_version, "step_id": node_id, "actor_id": command.actor_id,
            "dispatch_binding_digest": journal.binding_digest,
        }
        permitted = False
        for binding in policy_bindings:
            if not isinstance(binding, Mapping) or set(binding) != _FIELDS:
                continue
            maximum = binding["maximum_attempts"]
            if (type(maximum) is not int or not 2 <= maximum <= min(10, plan.budget.max_attempts)
                    or state.attempts.get(node_id, 0) >= maximum):
                continue
            if all(binding[name] == value for name, value in expected.items()):
                permitted = True
                break
        if not permitted:
            return False, "native_retry_uncertain_dispatch_policy_required"
    return True, "native_retry_uncertain_dispatch_preauthorized"


def production_uncertain_native_retry(command, *, plan, state):
    if not has_app_context() or str(current_app.config.get("ROLE") or "").lower() != "hub":
        return False, "native_retry_hub_policy_context_required"
    try:
        from agent.database import engine

        return authorize_uncertain_native_retry(
            command, plan=plan, state=state, engine=engine,
            policy_bindings=current_app.config.get("ANANTA_NATIVE_UNCERTAIN_RETRY_POLICY"),
        )
    except Exception:
        return False, "native_retry_dispatch_policy_unavailable"
