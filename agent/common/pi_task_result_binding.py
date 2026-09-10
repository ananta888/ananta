"""Small fail-closed Pi Task discriminator shared by repository boundaries."""

from collections.abc import Mapping

PI_RESULT_RECEIPT = "pi_native_result_receipt"


def task_value(task, field):
    return task.get(field) if isinstance(task, Mapping) else getattr(task, field, None)


def is_pi_task(task) -> bool:
    context = task_value(task, "worker_execution_context") or {}
    command = context.get("native_node_command") if isinstance(context, Mapping) else None
    node = command.get("node") if isinstance(command, Mapping) else None
    verification = task_value(task, "verification_status") or {}
    return (
        task_value(task, "task_kind") == "pi_coding_agent"
        or (isinstance(node, Mapping) and node.get("task_kind") == "pi_coding_agent")
        or (isinstance(verification, Mapping) and PI_RESULT_RECEIPT in verification)
    )


def require_pi_completion_policy(authoritative, candidate) -> None:
    if (is_pi_task(authoritative) or is_pi_task(candidate)) and (
        task_value(candidate, "status") in {"completed", "failed", "cancelled"}
        or task_value(authoritative, "status") in {"completed", "failed", "cancelled"}
    ):
        raise RuntimeError("pi_native_result_completion_policy_unavailable")
