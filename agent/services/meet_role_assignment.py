"""Hub-only exact publisher assignment admission and current snapshot fence."""

from typing import Protocol

from agent.models.meet_organization_topology import MeetTopologyScope
from agent.models.meet_role_assignment import MeetRoleFacts, binding_projection, publisher_origin
from agent.services.meet_contract import MeetError
from agent.services.meet_task_scope import organization_tuple


class RoleAssignmentRows(Protocol):
    def read(
        self, scope: MeetTopologyScope, publisher_url: str, assignment_id: str | None = None
    ) -> MeetRoleFacts | None: ...


class MeetRoleAssignments:
    def __init__(self, rows: RoleAssignmentRows):
        self.rows = rows

    def resolve(self, tenant, project, fields, publisher_url):
        """Read current eligible facts without reserving or fabricating a Task."""
        if not fields.get("role_slot_id"):
            return None
        try:
            publisher_origin(publisher_url)
            scope = MeetTopologyScope(tenant, project, **fields)
            facts = self.rows.read(scope, publisher_url)
            if not isinstance(facts, MeetRoleFacts) or facts.publisher_url != publisher_url:
                raise ValueError()
            facts.require_eligible()
            return scope, facts
        except Exception:
            raise MeetError("meet_dialog_assignment_denied", 403) from None

    def admit(self, task_id, tenant, project, context, fields, publisher_url):
        resolved = self.resolve(tenant, project, fields, publisher_url)
        if resolved is None:
            return None
        scope, facts = resolved
        try:
            return binding_projection(
                task_id, context["binding_task_id"], context["lease_id"], context["runtime_id"], scope, facts
            )
        except Exception:
            raise MeetError("meet_dialog_assignment_denied", 403) from None

    def require_current(self, task):
        if getattr(task, "task_kind", None) != "meet_dialog_session":
            return
        try:
            fields = organization_tuple(task)
            execution = task.worker_execution_context
            saved = execution.get("meet_role_assignment")
            if not fields.get("role_slot_id"):
                if saved is not None:
                    raise ValueError()
                return
            if not isinstance(saved, dict):
                raise ValueError()
            scope = MeetTopologyScope(task.tenant_id, task.project_id, **fields)
            publisher = publisher_origin(saved["publisher_url"])
            if task.assigned_agent_url != publisher:
                raise ValueError()
            facts = self.rows.read(scope, publisher, saved["assignment_id"])
            if not isinstance(facts, MeetRoleFacts) or facts.publisher_url != publisher:
                raise ValueError()
            context = execution["meet_dialog"]
            expected = binding_projection(
                task.id, task.parent_task_id, context["lease_id"], context["runtime_id"], scope, facts
            )
            if saved != expected:
                raise ValueError()
            return expected
        except Exception:
            raise MeetError("meet_dialog_assignment_revoked", 403) from None


def get_meet_role_assignments():
    from agent.repositories.meet_role_assignment import SqlMeetRoleAssignments

    return MeetRoleAssignments(SqlMeetRoleAssignments())
