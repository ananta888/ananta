"""Mutable, write-free draft state for one topology patch evaluation.

The evaluator interprets patch operations in order; later operations observe
the planned effects of earlier ones.  This module owns only that draft state
(and its diagnostics), so the evaluator's per-operation interpreters stay
small and independently readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.models.organization_models import OrganizationDiagnostic


@dataclass(slots=True)
class TopologyPatchDraft:
    """Ordered interpreter state shared across the operations of one patch."""

    units: dict[str, dict[str, Any]]
    slots: dict[str, dict[str, Any]]
    assignments: list[dict[str, Any]]
    historical_assignments: dict[tuple[str, str], Any]
    relations: dict[str, dict[str, Any]]
    reserved_stable_keys: set[str]
    reserved_slot_keys: set[tuple[str, str]]
    reserved_node_ids: set[str]
    reserved_relation_keys: set[str]
    reserved_relation_identities: set[tuple[str, str, str]]
    global_assignment_counts: dict[str, int]
    diagnostics: list[OrganizationDiagnostic] = field(default_factory=list)
    planned_writes: list[str] = field(default_factory=list)

    @classmethod
    def from_state(cls, state) -> TopologyPatchDraft:
        return cls(
            units={
                row.id: {
                    "id": row.id,
                    "key": row.unit_key,
                    "kind": row.unit_kind,
                    "parent_id": row.parent_unit_id,
                    "lifecycle": row.lifecycle,
                    "team_ref": (
                        f"{row.team_blueprint_key}@{row.team_blueprint_version}"
                        if row.team_blueprint_key and row.team_blueprint_version
                        else None
                    ),
                }
                for row in state.units
                if row.lifecycle != "archived"
            },
            slots={
                row.id: {
                    "id": row.id,
                    "unit_id": row.unit_id,
                    "key": row.slot_key,
                    "required": row.required,
                    "default_count": row.default_count,
                    "max_count": row.max_count,
                    "assignment_policy": dict(row.assignment_policy or {}),
                    "sod": dict(row.separation_of_duties or {}),
                    "lifecycle": row.lifecycle,
                }
                for row in state.role_slots
                if row.lifecycle != "archived"
            },
            assignments=[
                {"id": row.id, "slot_id": row.role_slot_id, "agent_id": row.agent_url}
                for row in state.assignments
                if row.lifecycle == "active"
            ],
            historical_assignments={
                (row.role_slot_id, row.agent_url): row for row in state.assignments if row.lifecycle != "active"
            },
            relations={
                row.id: {
                    "id": row.id,
                    "key": row.relation_key,
                    "kind": row.kind,
                    "source_id": row.source_unit_id,
                    "target_id": row.target_unit_id,
                    "dependency_policy": row.dependency_policy,
                }
                for row in state.relations
                if row.lifecycle == "active"
            },
            reserved_stable_keys={row.unit_key for row in state.units},
            reserved_slot_keys={(row.unit_id, row.slot_key) for row in state.role_slots},
            reserved_node_ids={row.id for row in (*state.units, *state.role_slots)},
            reserved_relation_keys={row.relation_key for row in state.relations},
            reserved_relation_identities={
                (row.kind, row.source_unit_id, row.target_unit_id)
                for row in state.relations
                if row.lifecycle == "active"
            },
            global_assignment_counts=dict(state.global_assignment_count_by_agent),
        )

    def issue(self, path: str, code: str, message: str, *, severity: str = "blocker", **details: Any) -> None:
        self.diagnostics.append(
            OrganizationDiagnostic(
                path=path,
                reason_code=code,
                human_message=message,
                severity=severity,
                details=details,
            )
        )

    def plan(self, *writes: str) -> None:
        self.planned_writes.extend(writes)

    def end_assignment(self, assignment: dict[str, Any]) -> None:
        """Drop one active assignment and release its agent-global capacity unit."""
        self.assignments.remove(assignment)
        agent_id = assignment["agent_id"]
        self.global_assignment_counts[agent_id] = max(
            0,
            int(self.global_assignment_counts.get(agent_id, 0)) - 1,
        )

    @property
    def has_blocker(self) -> bool:
        return any(row.severity == "blocker" for row in self.diagnostics)


def role_slot_draft_row(*, slot_id: str, unit_id: str, key, value) -> dict[str, Any]:
    """Project a role-slot definition (patch value or blueprint slot) into a draft row."""
    return {
        "id": slot_id,
        "unit_id": unit_id,
        "key": key,
        "required": value.required,
        "default_count": value.default_count,
        "max_count": value.max_count,
        "assignment_policy": value.assignment_policy.model_dump(mode="json"),
        "sod": value.separation_of_duties.model_dump(mode="json"),
        "lifecycle": "active",
    }


__all__ = ["TopologyPatchDraft", "role_slot_draft_row"]
