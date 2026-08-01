from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from agent.db_models.projects import ProjectDB, ProjectMembershipDB


class ProjectRepository:
    """Transaction-scoped persistence for the project aggregate."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tenant_id: str, project_id: str) -> Optional[ProjectDB]:
        return self._session.get(ProjectDB, (tenant_id, project_id))

    def get_by_team_id(self, team_id: str) -> Optional[ProjectDB]:
        statement = select(ProjectDB).where(ProjectDB.team_id == team_id)
        return self._session.exec(statement).first()

    def list_visible(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        tenant_admin: bool,
        include_archived: bool,
    ) -> Sequence[ProjectDB]:
        statement = select(ProjectDB).where(ProjectDB.tenant_id == tenant_id)
        if not tenant_admin:
            statement = statement.join(
                ProjectMembershipDB,
                sa.and_(
                    ProjectMembershipDB.tenant_id == ProjectDB.tenant_id,
                    ProjectMembershipDB.project_id == ProjectDB.project_id,
                ),
            ).where(
                ProjectMembershipDB.subject_id == subject_id,
                ProjectMembershipDB.state == "active",
            )
        if not include_archived:
            statement = statement.where(ProjectDB.status == "active")
        statement = statement.order_by(ProjectDB.name, ProjectDB.project_id)
        return list(self._session.exec(statement).all())

    def add(self, project: ProjectDB) -> None:
        self._session.add(project)

    def get_membership(
        self,
        tenant_id: str,
        project_id: str,
        subject_id: str,
    ) -> Optional[ProjectMembershipDB]:
        return self._session.get(
            ProjectMembershipDB,
            (tenant_id, project_id, subject_id),
        )

    def list_members(
        self,
        tenant_id: str,
        project_id: str,
    ) -> Sequence[ProjectMembershipDB]:
        statement = (
            select(ProjectMembershipDB)
            .where(
                ProjectMembershipDB.tenant_id == tenant_id,
                ProjectMembershipDB.project_id == project_id,
            )
            .order_by(ProjectMembershipDB.subject_id)
        )
        return list(self._session.exec(statement).all())

    def count_active_owners(self, tenant_id: str, project_id: str) -> int:
        statement = select(sa.func.count()).select_from(ProjectMembershipDB).where(
            ProjectMembershipDB.tenant_id == tenant_id,
            ProjectMembershipDB.project_id == project_id,
            ProjectMembershipDB.role == "owner",
            ProjectMembershipDB.state == "active",
        )
        return int(self._session.exec(statement).one())

    def add_membership(self, membership: ProjectMembershipDB) -> None:
        self._session.add(membership)

    def update_project_if_version(
        self,
        *,
        tenant_id: str,
        project_id: str,
        expected_lock_version: int,
        values: Mapping[str, object],
    ) -> bool:
        statement = (
            sa.update(ProjectDB)
            .where(
                ProjectDB.tenant_id == tenant_id,
                ProjectDB.project_id == project_id,
                ProjectDB.lock_version == expected_lock_version,
            )
            .values(**dict(values))
        )
        result = self._session.execute(statement)
        return int(result.rowcount or 0) == 1

    def update_membership_if_version(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_id: str,
        expected_lock_version: int,
        values: Mapping[str, object],
    ) -> bool:
        statement = (
            sa.update(ProjectMembershipDB)
            .where(
                ProjectMembershipDB.tenant_id == tenant_id,
                ProjectMembershipDB.project_id == project_id,
                ProjectMembershipDB.subject_id == subject_id,
                ProjectMembershipDB.lock_version == expected_lock_version,
            )
            .values(**dict(values))
        )
        result = self._session.execute(statement)
        return int(result.rowcount or 0) == 1
