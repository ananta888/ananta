"""Compatibility facade for the layer-neutral recovery result boundary."""

from agent.common.recovery_result_write_boundary import (
    DeferredRecoveryTaskWrites,
    defer_recovery_task_writes,
    defer_task_compare_and_set,
    defer_task_repository_save,
    defer_task_status_mutation,
    defer_task_verification_mutation,
)

__all__ = [
    "DeferredRecoveryTaskWrites",
    "defer_recovery_task_writes",
    "defer_task_compare_and_set",
    "defer_task_repository_save",
    "defer_task_status_mutation",
    "defer_task_verification_mutation",
]
