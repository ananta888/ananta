"""Session-bound adapters for the guarded legacy Team delete use case."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session, select

from agent.db_models import GoalDB, TaskDB, TeamDB, TeamMemberDB
from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationTeamLinkDB,
)


@dataclass(frozen=True, slots=True)
class OrganizationTeamBinding:
    tenant_id: str
    project_id: str
    organization_id: str
    organization_lifecycle: str
    link_lifecycle: str


class SessionBoundTeamDeleteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_team(self, team_id: str) -> TeamDB | None:
        statement = select(TeamDB).where(TeamDB.id == team_id)
        if self._session.bind and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return self._session.exec(statement).one_or_none()

    def delete(self, team: TeamDB) -> None:
        self._session.delete(team)


class SessionBoundOrganizationTeamLinkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_bindings(self, team_id: str) -> list[OrganizationTeamBinding]:
        statement = (
            select(OrganizationTeamLinkDB, OrganizationInstanceDB)
            .join(
                OrganizationInstanceDB,
                (OrganizationInstanceDB.tenant_id == OrganizationTeamLinkDB.tenant_id)
                & (OrganizationInstanceDB.project_id == OrganizationTeamLinkDB.project_id)
                & (OrganizationInstanceDB.organization_id == OrganizationTeamLinkDB.organization_id),
            )
            .where(OrganizationTeamLinkDB.team_id == team_id)
            .order_by(
                OrganizationTeamLinkDB.tenant_id,
                OrganizationTeamLinkDB.project_id,
                OrganizationTeamLinkDB.organization_id,
            )
        )
        if self._session.bind and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return [
            OrganizationTeamBinding(
                tenant_id=link.tenant_id,
                project_id=link.project_id,
                organization_id=link.organization_id,
                organization_lifecycle=organization.lifecycle,
                link_lifecycle=link.lifecycle,
            )
            for link, organization in self._session.exec(statement).all()
        ]


class SessionBoundOrganizationDeleteAuthority:
    def __init__(self, session: Session) -> None:
        self._session = session

    def can_manage(self, *, principal, binding: OrganizationTeamBinding) -> bool:
        if principal.is_hub_admin:
            return True
        if (
            not principal.principal_id
            or principal.tenant_id != binding.tenant_id
            or principal.project_id != binding.project_id
        ):
            return False
        membership = self._session.exec(
            select(OrganizationMembershipDB).where(
                OrganizationMembershipDB.tenant_id == binding.tenant_id,
                OrganizationMembershipDB.project_id == binding.project_id,
                OrganizationMembershipDB.organization_id == binding.organization_id,
                OrganizationMembershipDB.principal_id == principal.principal_id,
                OrganizationMembershipDB.membership_kind == "organization_admin",
            )
        ).one_or_none()
        return bool(
            membership is not None and (membership.expires_at is None or float(membership.expires_at) >= time.time())
        )


class SessionBoundTeamMemberDeleteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_team(self, team_id: str) -> list[TeamMemberDB]:
        return list(self._session.exec(select(TeamMemberDB).where(TeamMemberDB.team_id == team_id)).all())

    def delete_all(self, members: list[TeamMemberDB]) -> None:
        for member in members:
            self._session.delete(member)


class SessionBoundTaskTeamRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_for_team(self, team_id: str) -> list[TaskDB]:
        statement = select(TaskDB).where(TaskDB.team_id == team_id).order_by(TaskDB.id)
        if self._session.bind and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return list(self._session.exec(statement).all())

    @staticmethod
    def clear_team(tasks: list[TaskDB]) -> None:
        for task in tasks:
            task.team_id = None


class SessionBoundGoalTeamRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_for_team(self, team_id: str) -> list[GoalDB]:
        statement = select(GoalDB).where(GoalDB.team_id == team_id).order_by(GoalDB.id)
        if self._session.bind and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return list(self._session.exec(statement).all())

    @staticmethod
    def clear_team(goals: list[GoalDB]) -> None:
        for goal in goals:
            goal.team_id = None


class SqlOrganizationTeamDeletionUnitOfWork:
    """Own exactly one database transaction for guard, clears and delete."""

    def __init__(self, session: Session | None = None) -> None:
        self._owned_session = session is None
        if session is None:
            from agent.database import engine

            session = Session(engine)
        self.session = session
        self.teams = SessionBoundTeamDeleteRepository(session)
        self.organization_links = SessionBoundOrganizationTeamLinkRepository(session)
        self.authority = SessionBoundOrganizationDeleteAuthority(session)
        self.members = SessionBoundTeamMemberDeleteRepository(session)
        self.tasks = SessionBoundTaskTeamRepository(session)
        self.goals = SessionBoundGoalTeamRepository(session)

    def __enter__(self) -> "SqlOrganizationTeamDeletionUnitOfWork":
        # SQLite has no row locks. Acquiring its write reservation before the
        # first read closes the check/link-insert/delete race. Raw dialect
        # handling remains in this infrastructure adapter, not the service.
        if self.session.bind and self.session.bind.dialect.name == "sqlite":
            self.session.exec(text("BEGIN IMMEDIATE"))
        return self

    def flush(self) -> None:
        self.session.flush()

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            if self._owned_session:
                self.session.close()
        return False


__all__ = [
    "OrganizationTeamBinding",
    "SqlOrganizationTeamDeletionUnitOfWork",
]
