"""At most four exact-scope metadata reads; no role policy or task mutation."""

from sqlmodel import Session, select

from agent.db_models import OrganizationRoleSlotDB, OrganizationTeamLinkDB, OrganizationUnitDB, TeamDB
from agent.models.meet_organization_topology import MeetTopologyScope, MeetTopologySnapshot


def _scope(model, scope):
    return (
        model.tenant_id == scope.tenant_id,
        model.project_id == scope.project_id,
        model.organization_id == scope.organization_id,
    )


class SqlMeetOrganizationTopology:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory if session_factory is not None else self._session

    @staticmethod
    def _session():
        from agent.database import engine

        return Session(engine)

    def read(self, scope: MeetTopologyScope) -> MeetTopologySnapshot:
        with self.session_factory() as session:
            unit = (
                session.exec(
                    select(OrganizationUnitDB.lifecycle)
                    .where(*_scope(OrganizationUnitDB, scope), OrganizationUnitDB.id == scope.unit_id)
                    .limit(2)
                ).one_or_none()
                if scope.unit_id
                else None
            )
            team = (
                session.exec(
                    select(OrganizationTeamLinkDB.lifecycle, OrganizationTeamLinkDB.unit_id)
                    .where(*_scope(OrganizationTeamLinkDB, scope), OrganizationTeamLinkDB.team_id == scope.team_id)
                    .limit(2)
                ).one_or_none()
                if scope.team_id
                else None
            )
            active = (
                session.exec(select(TeamDB.is_active).where(TeamDB.id == scope.team_id).limit(2)).one_or_none()
                if team is not None
                else None
            )
            role = (
                session.exec(
                    select(OrganizationRoleSlotDB.lifecycle, OrganizationRoleSlotDB.unit_id)
                    .where(*_scope(OrganizationRoleSlotDB, scope), OrganizationRoleSlotDB.id == scope.role_slot_id)
                    .limit(2)
                ).one_or_none()
                if scope.role_slot_id
                else None
            )
        return MeetTopologySnapshot(
            unit_lifecycle=unit,
            team_lifecycle=team[0] if team is not None else None,
            team_unit_id=team[1] if team is not None else None,
            team_active=active,
            role_lifecycle=role[0] if role is not None else None,
            role_unit_id=role[1] if role is not None else None,
        )
