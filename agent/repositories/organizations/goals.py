"""Session-bound persistence for Organization root Goals."""

from __future__ import annotations

from sqlmodel import Session, select

from agent.db_models import GoalDB


class SqlOrganizationGoalRepository:
    """Keep Organization Goal writes inside the caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: GoalDB) -> GoalDB:
        self._session.add(row)
        return row

    def get_scoped(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        goal_id: str,
        *,
        for_update: bool = False,
    ) -> GoalDB | None:
        statement = select(GoalDB).where(
            GoalDB.id == goal_id,
            GoalDB.tenant_id == tenant_id,
            GoalDB.project_id == project_id,
            GoalDB.organization_id == organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).one_or_none()


__all__ = ["SqlOrganizationGoalRepository"]
