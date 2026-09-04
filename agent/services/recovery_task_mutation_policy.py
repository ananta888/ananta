"""Compatibility facade for the infrastructure-free Recovery mutation policy."""

from agent.common.recovery_task_mutation_policy import (
    RecoveryTaskMutationConflict,
    RecoveryTaskRole,
    ensure_external_recovery_mutation_allowed,
    is_active_recovery_task,
    recovery_task_role,
)

__all__ = [
    "RecoveryTaskMutationConflict",
    "RecoveryTaskRole",
    "ensure_external_recovery_mutation_allowed",
    "is_active_recovery_task",
    "recovery_task_role",
]
