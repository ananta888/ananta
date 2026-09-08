"""Hub parent/scope lifecycle fence, independent of grants and media execution."""

import re
from typing import Protocol

from agent.services.meet_contract import MeetError
from agent.services.meet_task_scope import organization_tuple as organization_tuple
from agent.services.task_state_machine_service import ACTIVE_TASK_STATUSES


class TaskLookup(Protocol):
    def get_by_id(self, task_id: str): ...


class OrganizationGate(Protocol):
    def evaluate(self, task): ...


class MeetDialogLifecycle:
    def __init__(self, tasks: TaskLookup, organization_gate: OrganizationGate | None = None):
        self.tasks = tasks
        self.organization_gate = organization_gate

    def _require_organization(self, task, scope):
        if not scope.get("organization_id"):
            return
        try:
            gate = self.organization_gate
            if gate is None:
                from agent.services.meet_organization_topology import get_meet_organization_topology_gate

                gate = get_meet_organization_topology_gate()
            allowed = gate.evaluate(task).allowed
        except Exception:
            raise MeetError("meet_dialog_lifecycle_unavailable", 403) from None
        if allowed is not True:
            raise MeetError("meet_dialog_organization_inactive", 403)

    def scope_for_parent(self, tenant, project, parent_id):
        if parent_id == "":
            return {}
        if not isinstance(parent_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", parent_id):
            raise MeetError("meet_dialog_parent_inactive", 403)
        try:
            parent = self.tasks.get_by_id(parent_id)
        except Exception:
            raise MeetError("meet_dialog_lifecycle_unavailable", 403) from None
        if (
            parent is None
            or getattr(parent, "id", None) != parent_id
            or getattr(parent, "tenant_id", None) != tenant
            or getattr(parent, "project_id", None) != project
            or getattr(parent, "archived", False) is not False
            or not isinstance(getattr(parent, "status", None), str)
            or getattr(parent, "status", None) not in ACTIVE_TASK_STATUSES
        ):
            raise MeetError("meet_dialog_parent_inactive", 403)
        scope = organization_tuple(parent)
        self._require_organization(parent, scope)
        return scope

    def require_current(self, task, parent_id):
        if (
            (getattr(task, "parent_task_id", None) or "") != parent_id
            or parent_id
            and getattr(task, "id", None) == parent_id
        ):
            raise MeetError("meet_dialog_parent_changed", 403)
        scope = organization_tuple(task)
        if parent_id:
            parent_scope = self.scope_for_parent(task.tenant_id, task.project_id, parent_id)
            if scope != parent_scope:
                raise MeetError("meet_dialog_parent_scope_changed", 403)
        else:
            self._require_organization(task, scope)
