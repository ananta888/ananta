"""Native Task discriminator and fail-closed repository composition boundary."""

from collections.abc import Mapping

NATIVE_RESULT_RECEIPT = "native_result_receipt"


def task_value(task, field):
    return task.get(field) if isinstance(task, Mapping) else getattr(task, field, None)


def is_native_task(task):
    context = task_value(task, "worker_execution_context") or {}
    verification = task_value(task, "verification_status") or {}
    return (
        task_value(task, "derivation_reason") == "native_graph_hub_delegation"
        or isinstance(context, Mapping) and (
            context.get("runtime_path") == "native_graph_node" or "native_node_command" in context
        )
        or isinstance(verification, Mapping) and NATIVE_RESULT_RECEIPT in verification
    )


def require_native_completion_policy(authoritative, candidate):
    if not (is_native_task(authoritative) or is_native_task(candidate)):
        return
    verification = task_value(candidate, "verification_status") or {}
    if (
        task_value(candidate, "status") in {"completed", "failed", "cancelled"}
        or isinstance(verification, Mapping) and (
            "native_node_result" in verification or "workflow_adapter_task_result" in verification
        )
    ):
        raise RuntimeError("native_result_completion_policy_unavailable")
