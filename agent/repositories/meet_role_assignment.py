"""Exact-scope assignment/registration read; no token, role overlays or task writes."""

from sqlmodel import Session, select

from agent.db_models import AgentInfoDB, OrganizationInstanceDB, OrganizationRoleAssignmentDB, OrganizationRoleSlotDB
from agent.models.meet_role_assignment import MeetRoleFacts, bounded_json


class SqlMeetRoleAssignments:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory if session_factory is not None else self._session

    @staticmethod
    def _session():
        from agent.database import engine

        return Session(engine)

    def read(self, scope, publisher_url, assignment_id=None):
        a, r, w = OrganizationRoleAssignmentDB, OrganizationRoleSlotDB, AgentInfoDB
        o = OrganizationInstanceDB
        query = (
            select(
                a.id,
                a.agent_url,
                a.lifecycle,
                a.assigned_at,
                a.ended_at,
                w.registration_validated,
                w.status,
                w.role,
                w.authorized_capabilities,
                w.execution_limits,
                r.assignment_policy,
                o.lock_version,
                o.definition_revision,
                o.effective_limit_profile_hash,
            )
            .join(w, w.url == a.agent_url)
            .join(
                r,
                (r.id == a.role_slot_id)
                & (r.tenant_id == a.tenant_id)
                & (r.project_id == a.project_id)
                & (r.organization_id == a.organization_id),
            )
            .join(
                o,
                (o.organization_id == a.organization_id)
                & (o.tenant_id == a.tenant_id)
                & (o.project_id == a.project_id),
            )
            .where(
                a.tenant_id == scope.tenant_id,
                a.project_id == scope.project_id,
                a.organization_id == scope.organization_id,
                a.role_slot_id == scope.role_slot_id,
                a.agent_url == publisher_url,
                r.unit_id == scope.unit_id,
                r.lifecycle == "active",
                o.lifecycle == "active",
            )
        )
        if assignment_id is not None:
            query = query.where(a.id == assignment_id)
        with self.session_factory() as session:
            row = session.exec(query.limit(2)).one_or_none()
        if row is None:
            return None
        return MeetRoleFacts(*row[:8], *(bounded_json(value) for value in row[8:11]), *row[11:])
