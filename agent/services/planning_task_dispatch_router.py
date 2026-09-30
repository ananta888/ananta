"""Resolves and locks the final Organization assignment of a planned task before dispatch."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlmodel import select

from agent.db_models import (
    AgentInfoDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    TaskDB,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_routing_service import (
    OrganizationRoutingCandidate,
    OrganizationRoutingRequest,
    OrganizationRoutingService,
    infer_organization_assignment_duties,
)
from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)
from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
)


class PlanningTaskDispatchRouter:
    """Route one materialized task to an eligible, separation-of-duties safe assignment."""

    def __init__(
        self,
        *,
        routing_service: OrganizationRoutingService,
        assignment_eligibility: OrganizationAssignmentEligibilityService,
    ) -> None:
        self._routing = routing_service
        self._assignment_eligibility = assignment_eligibility

    def route(
        self,
        *,
        session,
        track: Any,
        task: TaskDB,
        target_agent_hint: str | None,
    ) -> dict[str, Any]:
        """Resolve and lock the final persisted assignment before queue CAS."""

        slot = session.exec(
            select(OrganizationRoleSlotDB).where(
                OrganizationRoleSlotDB.id == str(task.role_slot_id or ""),
                OrganizationRoleSlotDB.tenant_id == track.tenant_id,
                OrganizationRoleSlotDB.project_id == track.project_id,
                OrganizationRoleSlotDB.organization_id == track.organization_id,
                OrganizationRoleSlotDB.unit_id == str(task.unit_id or ""),
                OrganizationRoleSlotDB.lifecycle == "active",
            )
        ).one_or_none()
        team = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                OrganizationTeamLinkDB.project_id == track.project_id,
                OrganizationTeamLinkDB.organization_id == track.organization_id,
                OrganizationTeamLinkDB.unit_id == str(task.unit_id or ""),
                OrganizationTeamLinkDB.team_id == str(task.team_id or ""),
                OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
            )
        ).one_or_none()
        if slot is None or team is None:
            raise PlanningTransitionError("planning_routing_binding_invalid")

        statement = select(OrganizationRoleAssignmentDB).where(
            OrganizationRoleAssignmentDB.tenant_id == track.tenant_id,
            OrganizationRoleAssignmentDB.project_id == track.project_id,
            OrganizationRoleAssignmentDB.organization_id == track.organization_id,
            OrganizationRoleAssignmentDB.role_slot_id == slot.id,
            OrganizationRoleAssignmentDB.lifecycle == "active",
        )
        if supports_row_lock(session):
            statement = statement.with_for_update()
        assignments = list(session.exec(statement).all())
        agent_urls = sorted({row.agent_url for row in assignments if row.agent_url})
        agent_statement = select(AgentInfoDB).where(AgentInfoDB.url.in_(agent_urls))
        if supports_row_lock(session):
            agent_statement = agent_statement.with_for_update()
        agents = {row.url: row for row in (list(session.exec(agent_statement).all()) if agent_urls else [])}
        slot_policy = dict(slot.assignment_policy or {})
        required_capabilities = {
            str(value)
            for value in (list(task.required_capabilities or []) + list(slot_policy.get("required_capabilities") or []))
            if str(value)
        }
        forbidden_capabilities = {
            str(value) for value in list(slot_policy.get("forbidden_capabilities") or []) if str(value)
        }
        candidates: list[OrganizationRoutingCandidate] = []
        for assignment in assignments:
            agent = agents.get(assignment.agent_url)
            capacity_used = int(
                session.exec(
                    select(func.count())
                    .select_from(TaskDB)
                    .where(
                        TaskDB.assigned_agent_url == assignment.agent_url,
                        TaskDB.status.in_(("assigned", "in_progress")),
                    )
                ).one()
                or 0
            )
            eligibility = self._assignment_eligibility.evaluate(
                agent=agent,
                required_capabilities=required_capabilities,
                forbidden_capabilities=forbidden_capabilities,
                capacity_used=capacity_used,
                principal_kind_allowed="agent"
                in {str(value) for value in list(slot_policy.get("principal_kinds") or [])},
                write_access_required=bool(slot_policy.get("write_access_required", False)),
            )
            metadata = dict(assignment.assignment_metadata or {})
            limits = dict(getattr(agent, "execution_limits", None) or {})
            candidates.append(
                OrganizationRoutingCandidate(
                    agent_id=assignment.agent_url,
                    assignment_id=assignment.id,
                    organization_id=assignment.organization_id,
                    team_id=str(task.team_id or ""),
                    role_slot_id=assignment.role_slot_id,
                    capabilities=eligibility.capabilities,
                    backend=str(metadata.get("backend") or limits.get("backend") or "native"),
                    runtime_target=str(metadata.get("runtime_target") or limits.get("runtime_target") or "default"),
                    max_risk_level=str(metadata.get("max_risk_level") or limits.get("max_risk_level") or "medium"),
                    capacity_used=eligibility.capacity_used,
                    capacity_limit=eligibility.capacity_limit,
                    assignment_status=("active" if eligibility.allowed else "ineligible"),
                    duties=infer_organization_assignment_duties(
                        slot_key=slot.slot_key,
                        role_template_key=slot.role_template_key,
                        assignment_metadata=metadata,
                    ),
                )
            )

        all_assignment_rows = list(
            session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.tenant_id == track.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == track.project_id,
                    OrganizationRoleAssignmentDB.organization_id == track.organization_id,
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).all()
        )
        slot_ids = sorted({row.role_slot_id for row in all_assignment_rows})
        slots = {
            row.id: row
            for row in (
                list(session.exec(select(OrganizationRoleSlotDB).where(OrganizationRoleSlotDB.id.in_(slot_ids))).all())
                if slot_ids
                else []
            )
        }
        team_by_unit = {
            row.unit_id: row.team_id
            for row in session.exec(
                select(OrganizationTeamLinkDB).where(
                    OrganizationTeamLinkDB.tenant_id == track.tenant_id,
                    OrganizationTeamLinkDB.project_id == track.project_id,
                    OrganizationTeamLinkDB.organization_id == track.organization_id,
                    OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
                )
            ).all()
        }
        current_duties = tuple(
            DutyAssignment(
                principal_id=row.agent_url,
                role_slot_id=row.role_slot_id,
                team_id=str(team_by_unit.get(getattr(slots.get(row.role_slot_id), "unit_id", "")) or ""),
                duties=infer_organization_assignment_duties(
                    slot_key=slots[row.role_slot_id].slot_key,
                    role_template_key=slots[row.role_slot_id].role_template_key,
                    assignment_metadata=dict(row.assignment_metadata or {}),
                ),
            )
            for row in all_assignment_rows
            if row.role_slot_id in slots
        )
        worker_context = dict(task.worker_execution_context or {})
        hints = dict(worker_context.get("routing_hints") or {})
        decision = self._routing.decide(
            request=OrganizationRoutingRequest(
                organization_id=track.organization_id,
                unit_id=str(task.unit_id or ""),
                task_id=task.id,
                task_kind=str(task.task_kind or "implementation"),
                role_slot_id=slot.id,
                required_capabilities=frozenset(required_capabilities),
                allowed_team_ids=frozenset({str(task.team_id or "")}),
                allowed_backends=frozenset(
                    str(value) for value in list(worker_context.get("allowed_backends") or []) if str(value)
                ),
                allowed_runtime_targets=frozenset(
                    str(value) for value in list(worker_context.get("allowed_runtime_targets") or []) if str(value)
                ),
                risk_level=str(worker_context.get("risk_level") or "medium"),
                effective_policy_hash=track.policy_hash,
                target_role_hint=str(hints.get("target_role_hint") or "") or None,
                target_team_hint=str(hints.get("target_team_hint") or "") or None,
                target_agent_hint=(str(target_agent_hint or hints.get("target_agent_hint") or "") or None),
            ),
            candidates=candidates,
            current_duty_assignments=current_duties,
            sod_policy=SeparationOfDutiesPolicy.enterprise_default(revision=track.policy_hash[:16]),
        )
        if decision.status != "routable" or not all(
            (
                decision.selected_agent_id,
                decision.selected_assignment_id,
                decision.selected_team_id,
                decision.selected_role_slot_id,
            )
        ):
            raise PlanningTransitionError(f"planning_routing_blocked:{decision.reason_code}")
        if decision.selected_team_id != str(task.team_id or "") or decision.selected_role_slot_id != str(
            task.role_slot_id or ""
        ):
            raise PlanningTransitionError("planning_routing_binding_invalid")
        return {
            "schema": "organization_routing_decision.v1",
            "effective_policy_hash": track.policy_hash,
            "decision_hash": decision.policy_hash,
            "reason_code": decision.reason_code,
            "selected_agent_id": decision.selected_agent_id,
            "selected_assignment_id": decision.selected_assignment_id,
            "selected_team_id": decision.selected_team_id,
            "selected_role_slot_id": decision.selected_role_slot_id,
            "candidate_evaluations": [
                {
                    "agent_id": row.agent_id,
                    "assignment_id": row.assignment_id,
                    "team_id": row.team_id,
                    "allowed": row.allowed,
                    "exclusion_reasons": list(row.exclusion_reasons),
                    "capacity_used": row.capacity_used,
                    "capacity_limit": row.capacity_limit,
                }
                for row in decision.candidates
            ],
        }


def supports_row_lock(session) -> bool:
    return str(getattr(getattr(session.get_bind(), "dialect", None), "name", "")) == "postgresql"


__all__ = [
    "PlanningTaskDispatchRouter",
    "supports_row_lock",
]
