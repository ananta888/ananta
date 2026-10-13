"""Lease-bound Native Task cancellation through the existing status CAS port."""

from agent.services.native_task_ingestion import native_command_bound_to_lease
from agent.services.task_runtime_service import append_task_history_event
from agent.services.task_state_machine_service import ACTIVE_TASK_STATUSES, can_transition_to
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.lease_fencing import validate_sqlalchemy_lease


def cancel_native_task_fenced(*, repository, runtime, task_id, lease, tenant_id, run_id, reason):
    compare = getattr(repository, "compare_and_set_status_guarded", None)
    notify = getattr(runtime, "notify_native_task_cancelled", None)
    if not callable(compare) or not callable(notify):
        raise RuntimeError("bpmn_cancel_recipient_fencing_unavailable")

    def predicate(task):
        command = native_command_bound_to_lease(task, lease)
        if command.tenant_id != tenant_id or command.run_id != run_id:
            raise ValueError("native_hub_task_cancel_binding_mismatch")
        if task.status in {"completed", "failed", "cancelled"}:
            return False
        if not can_transition_to(task.status, "cancelled")[0]:
            raise ValueError("native_hub_task_cancel_transition_denied")
        return True

    def mutate(task):
        append_task_history_event(
            task, event_type="workflow_node_task_cancelled", actor="system:native-graph-orchestrator",
            details={"run_id": run_id, "reason": str(reason)[:240]},
        )

    result = compare(
        task_id, transaction_guard=lambda session: validate_sqlalchemy_lease(session, lease, lease.checkpoint),
        expected_statuses=ACTIVE_TASK_STATUSES | {"queued", "running", "completed", "failed", "cancelled"},
        target_status="cancelled", predicate=predicate, mutate=mutate,
    )
    if result.updated:
        notify(result.task, previous_status=result.previous_status)
    elif result.task is None or result.task.status not in {"completed", "failed", "cancelled"}:
        raise OptimisticConcurrencyError("native_hub_task_cancel_cas_conflict")
