from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class RoleProvider(Protocol):
    """Protocol for objects that can provide a role."""

    @property
    def role(self) -> str | None: ...


@dataclass(frozen=True)
class LeaseInfo:
    """Information about an active task lease."""

    agent_url: str
    lease_until: float
    idempotency_key: str | None = None


@dataclass(frozen=True)
class WorkerSelection:
    worker_url: str | None
    reasons: list[str]
    matched_capabilities: list[str]
    matched_roles: list[str]
    strategy: str
    # WFG-009: workflow routing provenance. All fields are optional and
    # default to None/empty so existing call sites stay unchanged.
    workflow_step_id: str | None = None
    workflow_step_role: str | None = None
    workflow_task_kind: str | None = None
    routing_origin: str | None = None
