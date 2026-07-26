"""Compatibility facade for the layer-neutral task mutation lock."""

from agent.common.task_mutation_lock import (
    TaskMutationLockPort,
    get_task_mutation_lock_port,
)

__all__ = [
    "TaskMutationLockPort",
    "get_task_mutation_lock_port",
]
