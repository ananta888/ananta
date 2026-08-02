"""Bind the enterprise reference workflow to one persisted Organization.

The service creates a Planning-Track candidate only.  Adoption,
materialization, routing, and dispatch continue through the existing Hub
planning control plane; no second runtime graph is introduced.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlmodel import Session, select

from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
)
from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogService,
)
from agent.services.planning_summary_engine import PlanningSummaryEngine
from agent.services.planning_track_pipeline_service import (
    evaluate_planning_quality_gates,
    validate_planning_track_with_details,
)


class OrganizationReferenceWorkflowError(ValueError):
    def __init__(self, reason_code: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(reason_code)


class OrganizationReferenceWorkflowService:
    """Resolve workflow selectors against authoritative topology rows."""

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory or self._default_session

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def preview_track_candidate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        workflow_key: str,
        workflow_version: int,
        goal: str,
        source_category_item_ids: Sequence[str],
        owner: str,
    ) -> dict[str, Any]:
        workflow = self._catalog.get_workflow_definition(workflow_key, workflow_version)
        if workflow is None:
            raise OrganizationReferenceWorkflowError("organization_reference_workflow_not_found")
        source_ids = tuple(dict.fromkeys(str(value or "").strip() for value in source_category_item_ids))
        if not source_ids or any(not value for value in source_ids):
            raise OrganizationReferenceWorkflowError("organization_workflow_category_lineage_required")
        with self._session_factory() as session:
            organization = session.exec(
                select(OrganizationInstanceDB)
                .where(OrganizationInstanceDB.tenant_id == tenant_id)
                .where(OrganizationInstanceDB.project_id == project_id)
                .where(OrganizationInstanceDB.organization_id == organization_id)
                .where(OrganizationInstanceDB.lifecycle.in_(("validated", "active", "paused")))
            ).first()
            if organization is None:
                raise OrganizationReferenceWorkflowError("organization_not_found")
            units = list(
                session.exec(
                    select(OrganizationUnitDB)
                    .where(OrganizationUnitDB.tenant_id == tenant_id)
                    .where(OrganizationUnitDB.project_id == project_id)
                    .where(OrganizationUnitDB.organization_id == organization_id)
                    .where(OrganizationUnitDB.unit_kind == "team")
                    .where(OrganizationUnitDB.lifecycle.in_(("planned", "active")))
                    .order_by(OrganizationUnitDB.unit_key, OrganizationUnitDB.id)
                ).all()
            )
            links = {
                row.unit_id: row
                for row in session.exec(
                    select(OrganizationTeamLinkDB)
                    .where(OrganizationTeamLinkDB.tenant_id == tenant_id)
                    .where(OrganizationTeamLinkDB.project_id == project_id)
                    .where(OrganizationTeamLinkDB.organization_id == organization_id)
                    .where(OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")))
                ).all()
            }
            slots_by_unit: dict[str, list[OrganizationRoleSlotDB]] = {}
            for slot in session.exec(
                select(OrganizationRoleSlotDB)
                .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
                .where(OrganizationRoleSlotDB.project_id == project_id)
                .where(OrganizationRoleSlotDB.organization_id == organization_id)
                .where(OrganizationRoleSlotDB.lifecycle == "active")
                .order_by(OrganizationRoleSlotDB.unit_id, OrganizationRoleSlotDB.slot_key)
            ).all():
                slots_by_unit.setdefault(slot.unit_id, []).append(slot)

        tasks: list[dict[str, Any]] = []
        task_ids_by_step: dict[str, list[str]] = {}
        milestones: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for step_index, raw_step in enumerate(list(workflow.get("steps") or []), start=1):
            step = dict(raw_step)
            step_id = str(step.get("step_id") or "").strip()
            selector = dict(step.get("target_team_selector") or {})
            target_ref = str(selector.get("team_blueprint_ref") or "").strip()
            cardinality = int(selector.get("cardinality") or 0)
            owner_ref = str(step.get("owner_role_ref") or "").strip()
            candidates = [
                unit
                for unit in units
                if f"{unit.team_blueprint_key}@{unit.team_blueprint_version}" == target_ref and unit.id in links
            ]
            selected = candidates[:cardinality]
            if not step_id or cardinality < 1 or len(selected) != cardinality:
                blockers.append(
                    {
                        "reason_code": "ORGANIZATION_WORKFLOW_TARGET_CARDINALITY_UNSATISFIED",
                        "step_id": step_id,
                        "team_blueprint_ref": target_ref,
                        "required": cardinality,
                        "available": len(candidates),
                    }
                )
                continue
            step_task_ids: list[str] = []
            dependency_ids = [
                task_id
                for dependency in list(step.get("depends_on") or [])
                for task_id in task_ids_by_step.get(str(dependency), [])
            ]
            for ordinal, unit in enumerate(selected, start=1):
                role_slot = next(
                    (
                        slot
                        for slot in slots_by_unit.get(unit.id, [])
                        if f"{slot.role_template_key}@{slot.role_template_version}" == owner_ref
                    ),
                    None,
                )
                if role_slot is None:
                    blockers.append(
                        {
                            "reason_code": "ORGANIZATION_WORKFLOW_OWNER_ROLE_UNAVAILABLE",
                            "step_id": step_id,
                            "unit_id": unit.id,
                            "owner_role_ref": owner_ref,
                        }
                    )
                    continue
                task_id = f"WF-{step_index:02d}-{step_id}-{ordinal:02d}"
                gate = dict(step.get("gate") or {})
                required_capabilities = sorted(
                    {
                        str(value)
                        for value in dict(role_slot.assignment_policy or {}).get("required_capabilities", [])
                        if str(value)
                    }
                )
                task = {
                    "id": task_id,
                    "title": str(step.get("title") or step_id),
                    "description": (
                        f"Execute reference workflow step {step_id} for Organization "
                        f"{organization_id} on team unit {unit.unit_key}."
                    ),
                    "status": "todo",
                    "priority": "P1" if gate.get("required") else "P2",
                    "risk": "high" if gate.get("required") else "medium",
                    "type": str(step.get("task_kind") or "implementation"),
                    "task_kind": str(step.get("task_kind") or "implementation"),
                    "depends_on": list(dict.fromkeys(dependency_ids)),
                    "acceptance_criteria": list(gate.get("acceptance_checks") or [])
                    or [f"Workflow step {step_id} produced every declared output."],
                    "required_capabilities": required_capabilities,
                    "expected_inputs": list(step.get("inputs") or []),
                    "expected_outputs": list(step.get("outputs") or []),
                    "source_category_item_ids": list(source_ids),
                    "organization_binding": {
                        "organization_id": organization_id,
                        "unit_id": unit.id,
                        "team_id": links[unit.id].team_id,
                        "role_slot_id": role_slot.id,
                    },
                    "gate": bool(gate.get("required")),
                    "verification_spec": {
                        "acceptance_checks": list(gate.get("acceptance_checks") or []),
                        "approval_role_ref": gate.get("approval_role_ref"),
                        "independent_principal_required": bool(gate.get("independent_principal_required")),
                        "failure_policy": str(
                            step.get("failure_policy") or workflow.get("default_failure_policy") or "block"
                        ),
                    },
                    **({"handoff_ref": str(step.get("handoff_ref"))} if step.get("handoff_ref") else {}),
                }
                tasks.append(task)
                step_task_ids.append(task_id)
            task_ids_by_step[step_id] = step_task_ids
            milestones.append(
                {
                    "id": f"M-{step_index:02d}-{step_id}",
                    "title": str(step.get("title") or step_id),
                    "task_ids": step_task_ids,
                    "status": "todo",
                }
            )
        if blockers:
            raise OrganizationReferenceWorkflowError(
                "organization_reference_workflow_unroutable",
                details={"blockers": blockers},
            )
        payload: dict[str, Any] = {
            "version": "1.0",
            "owner": str(owner or "hub:organization-workflow"),
            "track": f"{workflow_key}@{workflow_version}",
            "purpose": str(goal or "Execute the Organization reference workflow."),
            "goal": str(goal or "Execute the Organization reference workflow."),
            "status_scale": ["todo", "in_progress", "partial", "blocked", "done"],
            "priority_scale": ["P0", "P1", "P2", "P3"],
            "risk_scale": ["low", "medium", "high", "critical"],
            "source_category_item_ids": list(source_ids),
            "organization_id": organization_id,
            "definition_revision": organization.definition_revision,
            "workflow_ref": f"{workflow_key}@{workflow_version}",
            "milestones": milestones,
            "tasks": tasks,
            "critical_path_tasks": self._critical_path(tasks),
            "tasks_status_summary": {},
        }
        payload, summary_issues = PlanningSummaryEngine().recompute(payload)
        schema_issues = validate_planning_track_with_details(payload)
        quality = evaluate_planning_quality_gates(payload, large_goal_mode=True, small_goal_mode=False)
        blocking = [
            *summary_issues,
            *(str(value.get("reason_code") or value) for value in schema_issues),
            *(str(value.get("reason_code") or value) for value in list(quality.get("blocking_issues") or [])),
        ]
        if blocking:
            raise OrganizationReferenceWorkflowError(
                "organization_reference_workflow_track_invalid",
                details={"issues": blocking[:100]},
            )
        return {
            "artifact_id": f"organization-workflow:{organization_id}:{workflow_key}",
            "payload": payload,
            "definition_revision": organization.definition_revision,
            "workflow_ref": f"{workflow_key}@{workflow_version}",
            "team_blueprint_counts": dict(
                sorted(
                    Counter(
                        str(
                            next(
                                unit.team_blueprint_key
                                for unit in units
                                if unit.id == task["organization_binding"]["unit_id"]
                            )
                        )
                        for task in tasks
                    ).items()
                )
            ),
            "task_count": len(tasks),
            "gate_count": sum(bool(task.get("gate")) for task in tasks),
            "applicable": True,
            "diagnostics": [],
        }

    @staticmethod
    def _critical_path(tasks: Sequence[Mapping[str, Any]]) -> list[str]:
        # Every task belongs to the same bounded reference DAG.  Keeping all
        # IDs is conservative and deterministic; the planning summary engine
        # validates dependency consistency independently.
        return [str(task["id"]) for task in tasks]


__all__ = [
    "OrganizationReferenceWorkflowError",
    "OrganizationReferenceWorkflowService",
]
