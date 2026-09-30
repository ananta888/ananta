"""Organization topology bindings of planned tasks: unit/team/slot, role revision and proposal target refs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlmodel import select

from agent.db_models import (
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
)
from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
)


def resolve_task_binding(task: Mapping[str, Any]) -> dict[str, str]:
    nested = organization_binding_of(task)
    binding = {
        "unit_id": str(task.get("unit_id") or nested.get("unit_id") or "").strip(),
        "team_id": str(task.get("team_id") or nested.get("team_id") or "").strip(),
        "role_slot_id": str(task.get("role_slot_id") or nested.get("role_slot_id") or "").strip(),
    }
    if any(not value for value in binding.values()):
        raise PlanningTransitionError("planning_task_organization_binding_required")
    return binding


def organization_binding_of(task: Mapping[str, Any]) -> dict[str, Any]:
    value = task.get("organization_binding")
    return dict(value) if isinstance(value, Mapping) else {}


def restricted_task_evidence_refs(
    *,
    task: Mapping[str, Any],
    field: str,
    track_refs: list[str],
) -> list[str]:
    """Apply an optional task-level restriction without allowing expansion."""

    authoritative = {str(value) for value in track_refs if str(value)}
    if field not in task:
        return sorted(authoritative)
    raw = task.get(field)
    if not isinstance(raw, list):
        raise PlanningTransitionError("planning_task_evidence_allowlist_invalid")
    requested = {str(value) for value in raw if str(value)}
    if not requested.issubset(authoritative):
        raise PlanningTransitionError("planning_task_evidence_scope_expansion")
    return sorted(requested)


def resolve_role_template_ref(
    *,
    uow: PlanningControlUnitOfWork,
    track: Any,
    task: Mapping[str, Any],
    role_slot_id: str,
) -> str:
    """Resolve the authoritative role revision from the bound Hub slot."""

    assert uow.session is not None
    slot = uow.session.get(OrganizationRoleSlotDB, role_slot_id)
    if (
        slot is None
        or slot.tenant_id != track.tenant_id
        or slot.project_id != track.project_id
        or slot.organization_id != track.organization_id
    ):
        raise PlanningTransitionError("planning_task_role_slot_binding_invalid")
    resolved = f"{slot.role_template_key}@{slot.role_template_version}"
    declared = str(
        task.get("role_template_ref") or organization_binding_of(task).get("role_template_ref") or ""
    ).strip()
    if declared and declared != resolved:
        raise PlanningTransitionError("planning_task_role_template_binding_conflict")
    return resolved


def load_organization_topology_index(
    *,
    session,
    track: Any,
) -> dict[str, Any]:
    """Read authoritative proposal-hint refs once per materialization."""

    links = list(
        session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                OrganizationTeamLinkDB.project_id == track.project_id,
                OrganizationTeamLinkDB.organization_id == track.organization_id,
                OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
            )
        ).all()
    )
    unit_to_team = {row.unit_id: row.team_id for row in links}
    slots = list(
        session.exec(
            select(OrganizationRoleSlotDB).where(
                OrganizationRoleSlotDB.tenant_id == track.tenant_id,
                OrganizationRoleSlotDB.project_id == track.project_id,
                OrganizationRoleSlotDB.organization_id == track.organization_id,
                OrganizationRoleSlotDB.unit_id.in_(sorted(unit_to_team)),
                OrganizationRoleSlotDB.lifecycle == "active",
            )
        ).all()
    )
    slot_by_id = {row.id: row for row in slots}
    assignments = list(
        session.exec(
            select(OrganizationRoleAssignmentDB).where(
                OrganizationRoleAssignmentDB.tenant_id == track.tenant_id,
                OrganizationRoleAssignmentDB.project_id == track.project_id,
                OrganizationRoleAssignmentDB.organization_id == track.organization_id,
                OrganizationRoleAssignmentDB.role_slot_id.in_(sorted(slot_by_id)),
                OrganizationRoleAssignmentDB.lifecycle == "active",
            )
        ).all()
    )
    return {
        "unit_to_team": unit_to_team,
        "slots": slots,
        "assignments": assignments,
    }


def authorized_topology_refs(
    *,
    topology_index: Mapping[str, Any],
    unit_id: str,
    team_id: str,
    proposal_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose only Hub-known opaque refs allowed by the role target scope."""

    unit_to_team = dict(topology_index.get("unit_to_team") or {})
    target_scope = {str(value) for value in list(proposal_policy.get("target_scope") or []) if str(value)}
    if "same_organization" in target_scope:
        allowed_units = set(unit_to_team)
    elif "same_unit" in target_scope:
        allowed_units = {unit_id} if unit_id in unit_to_team else set()
    elif "same_team" in target_scope:
        allowed_units = {
            candidate_unit for candidate_unit, candidate_team in unit_to_team.items() if candidate_team == team_id
        }
    else:
        allowed_units = set()
    slots = [row for row in list(topology_index.get("slots") or []) if row.unit_id in allowed_units]
    allowed_slot_ids = {row.id for row in slots}
    assignments = [row for row in list(topology_index.get("assignments") or []) if row.role_slot_id in allowed_slot_ids]
    return {
        "schema": "organization_topology_refs.v1",
        "role_refs": sorted({f"{row.role_template_key}@{row.role_template_version}" for row in slots}),
        "team_refs": sorted({unit_to_team[value] for value in allowed_units}),
        # Assignment IDs are stable, opaque Hub references.  Worker URLs
        # are deliberately never exposed as proposal target identifiers.
        "agent_refs": sorted({row.id for row in assignments}),
        "worker_addresses_included": False,
    }


__all__ = [
    "authorized_topology_refs",
    "load_organization_topology_index",
    "organization_binding_of",
    "resolve_role_template_ref",
    "resolve_task_binding",
    "restricted_task_evidence_refs",
]
