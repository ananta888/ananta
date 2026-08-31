"""Role-bound automated reviewer selection for Organization workflow gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlmodel import select

from agent.db_models import (
    AgentInfoDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
)
from agent.services.organization_routing_service import (
    infer_organization_assignment_duties,
)
from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
    SeparationOfDutiesService,
)


@dataclass(frozen=True, slots=True)
class OrganizationWorkflowGateReviewer:
    assignment_id: str
    principal_id: str
    policy_id: str
    policy_revision: str
    policy_hash: str


class OrganizationWorkflowGateReviewerService:
    """Select a registered role Agent without direct or indirect self-review."""

    def __init__(self, separation_of_duties: SeparationOfDutiesService | None = None) -> None:
        self._sod = separation_of_duties or SeparationOfDutiesService()

    def select(self, *, task: Any, binding: dict[str, Any], session: Any) -> OrganizationWorkflowGateReviewer | None:
        policy = SeparationOfDutiesPolicy.enterprise_default(revision="1")
        policy_hash = self._sod.policy_hash(policy)
        role_key, role_version = str(binding["gate"]["approval_role_ref"]).rsplit("@", 1)
        slots = list(
            session.exec(
                select(OrganizationRoleSlotDB).where(
                    OrganizationRoleSlotDB.tenant_id == task.tenant_id,
                    OrganizationRoleSlotDB.project_id == task.project_id,
                    OrganizationRoleSlotDB.organization_id == task.organization_id,
                    OrganizationRoleSlotDB.role_template_key == role_key,
                    OrganizationRoleSlotDB.role_template_version == int(role_version),
                    OrganizationRoleSlotDB.lifecycle == "active",
                )
            ).all()
        )
        slot_by_id = {row.id: row for row in slots}
        if not slot_by_id:
            return None
        assignments = list(
            session.exec(
                select(OrganizationRoleAssignmentDB)
                .where(
                    OrganizationRoleAssignmentDB.tenant_id == task.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == task.project_id,
                    OrganizationRoleAssignmentDB.organization_id == task.organization_id,
                    OrganizationRoleAssignmentDB.role_slot_id.in_(slot_by_id),
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
                .order_by(OrganizationRoleAssignmentDB.id)
            ).all()
        )
        execution_principal = self._execution_principal(task=task, session=session)
        for assignment in assignments:
            metadata = dict(assignment.assignment_metadata or {})
            principal_id = str(metadata.get("principal_id") or "")
            agent = session.get(AgentInfoDB, assignment.agent_url)
            if (
                not principal_id
                or principal_id != assignment.agent_url
                or agent is None
                or agent.status != "online"
                or agent.registration_validated is not True
            ):
                continue
            if binding["gate"]["independent_principal_required"] is True and principal_id == execution_principal:
                continue
            if self._has_conflict(
                principal_id=principal_id,
                approval_assignment=assignment,
                approval_slot=slot_by_id[assignment.role_slot_id],
                task=task,
                session=session,
                policy=policy,
            ):
                continue
            return OrganizationWorkflowGateReviewer(
                assignment_id=assignment.id,
                principal_id=principal_id,
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                policy_hash=policy_hash,
            )
        return None

    def _has_conflict(self, *, principal_id, approval_assignment, approval_slot, task, session, policy) -> bool:
        assignments = list(
            session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.tenant_id == task.tenant_id,
                    OrganizationRoleAssignmentDB.project_id == task.project_id,
                    OrganizationRoleAssignmentDB.organization_id == task.organization_id,
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).all()
        )
        duties: list[DutyAssignment] = []
        for assignment in assignments:
            metadata = dict(assignment.assignment_metadata or {})
            if str(metadata.get("principal_id") or "") != principal_id:
                continue
            slot = session.get(OrganizationRoleSlotDB, assignment.role_slot_id)
            if slot is None or slot.lifecycle != "active":
                continue
            duties.append(
                DutyAssignment(
                    principal_id=principal_id,
                    role_slot_id=slot.id,
                    team_id=str(slot.unit_id or ""),
                    duties=infer_organization_assignment_duties(
                        slot_key=slot.slot_key,
                        role_template_key=slot.role_template_key,
                        assignment_metadata=metadata,
                    ),
                )
            )
        approval_duties = {"independent_reviewer"}
        identity = ":".join(
            (
                str(approval_slot.slot_key or ""),
                str(approval_slot.role_template_key or ""),
                str(getattr(task, "task_kind", "") or ""),
            )
        ).lower()
        if "security" in identity:
            approval_duties.add("security_approver")
        if "release" in identity:
            approval_duties.add("go_no_go_approver")
        duties.append(
            DutyAssignment(
                principal_id=principal_id,
                role_slot_id=approval_assignment.role_slot_id,
                team_id=str(approval_slot.unit_id or ""),
                duties=frozenset(approval_duties),
            )
        )
        return not self._sod.evaluate(policy=policy, assignments=duties).allowed

    @staticmethod
    def _execution_principal(*, task: Any, session: Any) -> str:
        routing = dict(getattr(task, "worker_execution_context", None) or {}).get("organization_routing")
        if not isinstance(routing, dict):
            return ""
        assignment = session.get(
            OrganizationRoleAssignmentDB,
            str(routing.get("selected_assignment_id") or ""),
        )
        return str(dict(getattr(assignment, "assignment_metadata", None) or {}).get("principal_id") or "")


organization_workflow_gate_reviewer_service = OrganizationWorkflowGateReviewerService()


__all__ = [
    "OrganizationWorkflowGateReviewer",
    "OrganizationWorkflowGateReviewerService",
    "organization_workflow_gate_reviewer_service",
]
