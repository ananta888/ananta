"""Narrow Hub-to-Worker port for Organization planning dispatches."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PlanningDispatchEnvelope:
    """Durable data handed to one idempotent Worker-delegation adapter."""

    dispatch_intent_id: str
    idempotency_key: str
    lease_id: str
    attempt: int
    tenant_id: str
    project_id: str
    organization_id: str
    goal_id: str
    track_revision_id: str
    plan_task_id: str
    internal_task_id: str
    requested_worker_id: str | None
    task: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlanningDispatchAcceptance:
    """Minimal authoritative receipt returned by a delegation adapter."""

    worker_job_id: str
    assignment_id: str
    worker_id: str
    receipt: dict[str, Any] = field(default_factory=dict)


class PlanningWorkerDelegationPort(Protocol):
    """The Hub owns calls to this port; Workers never call one another."""

    def dispatch(
        self,
        envelope: PlanningDispatchEnvelope,
    ) -> PlanningDispatchAcceptance: ...


__all__ = [
    "PlanningDispatchAcceptance",
    "PlanningDispatchEnvelope",
    "PlanningWorkerDelegationPort",
]
