"""Meet-specific live topology policy; generic organization dispatch is unchanged."""

import re
from typing import Protocol

from agent.models.meet_organization_topology import MeetTopologyScope, MeetTopologySnapshot
from agent.services.meet_contract import MeetError
from agent.services.meet_task_scope import organization_tuple
from agent.services.organization_task_dispatch_gate_service import OrganizationTaskDispatchDecision


class TopologyRows(Protocol):
    def read(self, scope: MeetTopologyScope) -> MeetTopologySnapshot: ...


class OrganizationGate(Protocol):
    def evaluate(self, task) -> OrganizationTaskDispatchDecision: ...


def active_topology(scope: MeetTopologyScope, state: MeetTopologySnapshot) -> bool:
    if not isinstance(state, MeetTopologySnapshot):
        return False
    if scope.unit_id and state.unit_lifecycle != "active":
        return False
    if scope.team_id and (
        not scope.unit_id
        or state.team_lifecycle != "active"
        or state.team_unit_id != scope.unit_id
        or state.team_active is not True
    ):
        return False
    if scope.role_slot_id and (
        not scope.unit_id or state.role_lifecycle != "active" or state.role_unit_id != scope.unit_id
    ):
        return False
    return True


class MeetOrganizationTopologyGate:
    def __init__(self, organization: OrganizationGate, rows: TopologyRows):
        self.organization, self.rows = organization, rows

    def evaluate(self, task):
        try:
            fields = organization_tuple(task)
            tenant, project = getattr(task, "tenant_id", None), getattr(task, "project_id", None)
            if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,191}", v) for v in (tenant, project)):
                raise MeetError("meet_dialog_organization_scope_invalid", 403)
        except MeetError:
            return OrganizationTaskDispatchDecision(False, "meet_organization_scope_invalid")
        try:
            if self.organization.evaluate(task).allowed is not True:
                return OrganizationTaskDispatchDecision(False, "meet_organization_inactive")
            if not fields.get("organization_id"):
                return OrganizationTaskDispatchDecision(True, "meet_task_not_organization_scoped")
            if not any(fields.get(key) for key in ("unit_id", "team_id", "role_slot_id")):
                return OrganizationTaskDispatchDecision(True, "meet_organization_topology_allowed")
            scope = MeetTopologyScope(tenant_id=tenant, project_id=project, **fields)
            allowed = active_topology(scope, self.rows.read(scope))
        except Exception:
            raise ValueError("meet_organization_topology_unavailable") from None
        return OrganizationTaskDispatchDecision(
            allowed, "meet_organization_topology_allowed" if allowed else "meet_organization_topology_inactive"
        )


def get_meet_organization_topology_gate():
    from agent.repositories.meet_organization_topology import SqlMeetOrganizationTopology
    from agent.services.organization_task_dispatch_gate_service import get_organization_task_dispatch_gate_service

    return MeetOrganizationTopologyGate(get_organization_task_dispatch_gate_service(), SqlMeetOrganizationTopology())
