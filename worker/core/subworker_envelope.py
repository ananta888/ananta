"""SubworkerEnvelope: capability subset enforcement for delegated subworkers. AWF-T032, T034, T035."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

_MAX_DEPTH_LIMIT = 5
_MAX_CHILDREN_LIMIT = 10

_MUTATION_CAPABILITIES = frozenset({
    "shell_execute", "patch_apply", "memory_write", "file_write", "mcp_call",
})


@dataclass(frozen=True)
class SubworkerEnvelope:
    """Delegation contract from parent to child worker. AWF-T032.

    Child capabilities must be a strict subset of parent capabilities.
    Hub creates or approves this envelope — parent workers cannot mint new authority.
    """
    parent_execution_id: str
    child_task_id: str
    delegated_objective: str
    parent_capabilities: list[str]
    reduced_capability_snapshot: list[str]
    context_subset_ref: str
    audit_correlation_id: str
    timeout_seconds: float = 300.0
    max_depth: int = 3
    max_children: int = 5
    current_depth: int = 0
    deadline_at: float = field(default_factory=lambda: time.time() + 300.0)
    expected_artifacts: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Validate capability subset and limit constraints. AWF-T032, T034."""
        errors: list[str] = []
        parent_set = frozenset(self.parent_capabilities)
        child_set = frozenset(self.reduced_capability_snapshot)

        # T032: child caps must not escalate beyond parent
        escalated = child_set - parent_set
        if escalated:
            errors.append(f"subworker_capability_escalation:{sorted(escalated)}")

        # T034: depth limit
        if self.current_depth >= self.max_depth:
            errors.append(
                f"delegation_cycle_or_depth_limit:current={self.current_depth},max={self.max_depth}"
            )
        if self.max_depth > _MAX_DEPTH_LIMIT:
            errors.append(f"max_depth_exceeds_system_limit:{self.max_depth}>{_MAX_DEPTH_LIMIT}")
        if self.max_children > _MAX_CHILDREN_LIMIT:
            errors.append(
                f"max_children_exceeds_system_limit:{self.max_children}>{_MAX_CHILDREN_LIMIT}"
            )
        return errors

    def is_expired(self) -> bool:
        """T035: check whether the deadline has passed."""
        return time.time() > self.deadline_at

    def has_mutation_capability(self) -> bool:
        """T034: true if child capability set includes any mutation capability."""
        return bool(frozenset(self.reduced_capability_snapshot) & _MUTATION_CAPABILITIES)


def create_subworker_envelope(
    *,
    parent_execution_id: str,
    child_task_id: str,
    delegated_objective: str,
    parent_capabilities: list[str],
    reduced_capabilities: list[str],
    context_subset_ref: str,
    audit_correlation_id: str,
    timeout_seconds: float = 300.0,
    max_depth: int = 3,
    max_children: int = 5,
    current_depth: int = 0,
    expected_artifacts: list[str] | None = None,
) -> tuple[SubworkerEnvelope, list[str]]:
    """Create and validate a SubworkerEnvelope. Returns (envelope, errors). AWF-T032."""
    envelope = SubworkerEnvelope(
        parent_execution_id=parent_execution_id,
        child_task_id=child_task_id,
        delegated_objective=delegated_objective,
        parent_capabilities=list(parent_capabilities),
        reduced_capability_snapshot=list(reduced_capabilities),
        context_subset_ref=context_subset_ref,
        audit_correlation_id=audit_correlation_id,
        timeout_seconds=float(timeout_seconds),
        max_depth=int(max_depth),
        max_children=int(max_children),
        current_depth=int(current_depth),
        deadline_at=time.time() + float(timeout_seconds),
        expected_artifacts=list(expected_artifacts or []),
    )
    return envelope, envelope.validate()
