"""Adopt only an exact Hub-persisted delegation after a checkpoint interruption."""

from __future__ import annotations

from agent.services.workflow_runtime._serialization import canonical_json


def recover_submission(*, queue, grants, plan, request, node, ownership, input_data, require_active_grant=True):
    read = getattr(queue, "get_submission", None)
    if read is None or ownership.status not in {"active", "completed"}:
        return None
    command_id = f"ncmd:{request.run_id}:{node.node_id}:{ownership.attempt_id}"
    submission = read(command_id=command_id, tenant_id=plan.tenant_id, run_id=request.run_id)
    if submission is None:
        return None
    command = submission.command
    command.assert_valid()
    if (
        command.command_id != command_id
        or command.tenant_id != plan.tenant_id
        or command.run_id != request.run_id
        or command.workflow_id != plan.workflow_id
        or command.control_task_id != request.control_task_id
        or command.plan_hash != plan.plan_hash
        or command.policy_version != plan.policy_version
        or (command.correlation_id or command.run_id) != (request.correlation_id or request.run_id)
        or command.attempt_id != ownership.attempt_id
        or command.fencing_token != ownership.fencing_token
        or canonical_json(command.node.to_dict()) != canonical_json(node.to_dict())
        or canonical_json(command.input_data) != canonical_json(input_data)
        or not submission.receipt.accepted
        or submission.receipt.command_id != command_id
        or not submission.receipt.hub_task_id
    ):
        raise ValueError("native_submission_recovery_binding_mismatch")
    # Cancellation may recover an already-revoked receipt solely to finish its
    # durable cancellation. Execution recovery must always revalidate authority.
    if require_active_grant and not grants.revalidate(command.authorization):
        raise PermissionError("native_submission_recovery_grant_denied")
    return submission
