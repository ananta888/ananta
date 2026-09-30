"""Organization event envelope (dependency-free domain contract)."""

from __future__ import annotations

from dataclasses import dataclass

ORGANIZATION_EVENT_TYPES = frozenset(
    {
        "organization_instantiated",
        "team_started",
        "task_routed",
        "dependency_blocked",
        "dependency_released",
        "handoff_submitted",
        "handoff_accepted",
        "handoff_rejected",
        "handoff_needs_changes",
        "handoff_cancelled",
        "gate_opened",
        "gate_approved",
        "gate_rejected",
        "escalation_requested",
        "workflow_rework_requested",
        "workflow_loop_started",
        "workflow_loop_exhausted",
        "workflow_loop_completed",
        "budget_reserved",
        "budget_settled",
        "budget_exhausted",
        "organization_completed",
    }
)


@dataclass(frozen=True, slots=True)
class OrganizationEvent:
    event_id: str
    event_type: str
    organization_id: str
    definition_revision: str
    snapshot_hash: str
    correlation_id: str
    sequence: int
    occurred_at: str
    payload: dict[str, object]


__all__ = ["ORGANIZATION_EVENT_TYPES", "OrganizationEvent"]
