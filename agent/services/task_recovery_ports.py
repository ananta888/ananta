"""Narrow collaborator ports of the Hub recovery-planning saga steps.

The saga steps (approval saga, proposal, release, approval decision) used to
receive the whole ``TaskRecoveryPlanningService`` and call ~25 of its private
helpers. They now receive only explicit collaborators (ISP/DIP): the
:class:`RecoveryLocks` bundle, a :data:`ConditionalTaskUpdate` port, lazily
resolved service providers and - for cross-step calls - the other step
objects. Pure rules are imported from ``task_recovery_planning_rules``.

``TaskRecoveryPlanningService`` remains the composition root: it builds the
ports from its constructor providers (production defaults when a provider is
omitted) for each call, so instance-level test doubles of its compatibility
methods keep taking effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ContextManager

LockFactory = Callable[[str], ContextManager[Any]]

# ``conditional_update(task_id, status, *, expected_statuses, **values) -> bool``:
# the row-CAS status transition of one Hub task; ``True`` only when applied.
ConditionalTaskUpdate = Callable[..., bool]

# ``provider() -> service``: resolved at the point of use, like the historic
# ``service._repos()`` / ``service._approval_service()`` calls.
ServiceProvider = Callable[[], Any]

AuditSink = Callable[[str, dict[str, Any]], None]
PolicyBinding = Callable[[Any], tuple[list[str], bool, str]]


@dataclass(frozen=True)
class RecoveryLocks:
    """Lock ports of one recovery mutation.

    ``distributed_*`` and ``plan_mutation_lock`` yield ``True`` only when the
    lock was acquired; ``lock_for`` and ``source_mutation_lock`` are plain
    process-local context managers. Steps keep the historic acquisition order.
    """

    lock_for: LockFactory
    source_mutation_lock: LockFactory
    distributed_source_lock: LockFactory
    distributed_recovery_lock: LockFactory
    distributed_task_locks: Callable[[set[str] | list[str] | tuple[str, ...]], ContextManager[Any]]
    plan_mutation_lock: LockFactory
