from typing import List, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from agent.db_models import AgentSessionDB, ArchivedTaskDB, PolicySnapshotDB, TaskDB, ToolCallDB


def _engine():
    from agent.database import engine

    return engine


class TaskRepository:
    def get_all(self):
        with Session(_engine()) as session:
            return session.exec(select(TaskDB)).all()

    def get_by_id(self, task_id: str) -> Optional[TaskDB]:
        with Session(_engine()) as session:
            return session.get(TaskDB, task_id)

    def get_by_goal_id(self, goal_id: str) -> List[TaskDB]:
        with Session(_engine()) as session:
            return session.exec(select(TaskDB).where(TaskDB.goal_id == goal_id)).all()

    def save(self, task: TaskDB):
        with Session(_engine()) as session:
            task = session.merge(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        with Session(_engine()) as session:
            task = session.get(TaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def clear_team_assignments(self, team_id: str) -> int:
        with Session(_engine()) as session:
            statement = select(TaskDB).where(TaskDB.team_id == team_id)
            tasks = session.exec(statement).all()
            for task in tasks:
                task.team_id = None
                session.add(task)
            session.commit()
            return len(tasks)

    def get_old_tasks(self, cutoff: float):
        with Session(_engine()) as session:
            statement = select(TaskDB).where(TaskDB.created_at < cutoff)
            return session.exec(statement).all()

    def get_paged(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str = None,
        status_values: list[str] | None = None,
        agent: str = None,
        since: float = None,
        until: float = None,
    ):
        with Session(_engine()) as session:
            statement = select(TaskDB)
            if status:
                statement = statement.where(TaskDB.status == status)
            elif status_values:
                statement = statement.where(or_(*[TaskDB.status == val for val in status_values]))
            if agent:
                statement = statement.where(TaskDB.assigned_agent_url == agent)
            if since:
                statement = statement.where(TaskDB.created_at >= since)
            if until:
                statement = statement.where(TaskDB.created_at <= until)

            statement = statement.order_by(TaskDB.updated_at.desc()).offset(offset).limit(limit)
            return session.exec(statement).all()


class ArchivedTaskRepository:
    def get_all(self, limit: int = 100, offset: int = 0):
        with Session(_engine()) as session:
            statement = select(ArchivedTaskDB).order_by(ArchivedTaskDB.archived_at.desc()).offset(offset).limit(limit)
            return session.exec(statement).all()

    def get_by_id(self, task_id: str) -> Optional[ArchivedTaskDB]:
        with Session(_engine()) as session:
            return session.get(ArchivedTaskDB, task_id)

    def save(self, task: ArchivedTaskDB):
        with Session(_engine()) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        with Session(_engine()) as session:
            task = session.get(ArchivedTaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def delete_old(self, cutoff: float):
        with Session(_engine()) as session:
            from sqlmodel import delete

            statement = delete(ArchivedTaskDB).where(ArchivedTaskDB.archived_at < cutoff)
            session.exec(statement)
            session.commit()


class AgentSessionRepository:
    def get_all(self) -> List[AgentSessionDB]:
        with Session(_engine()) as session:
            statement = select(AgentSessionDB).order_by(AgentSessionDB.updated_at.desc())
            return session.exec(statement).all()

    def get_by_id(self, session_id: str) -> Optional[AgentSessionDB]:
        with Session(_engine()) as session:
            return session.get(AgentSessionDB, session_id)

    def get_by_task_id(self, task_id: str) -> List[AgentSessionDB]:
        with Session(_engine()) as session:
            statement = (
                select(AgentSessionDB)
                .where(AgentSessionDB.task_id == task_id)
                .order_by(AgentSessionDB.updated_at.desc())
            )
            return session.exec(statement).all()

    def save(self, agent_session: AgentSessionDB) -> AgentSessionDB:
        with Session(_engine()) as session:
            merged = session.merge(agent_session)
            session.commit()
            session.refresh(merged)
            return merged


class ToolCallRepository:
    def get_by_id(self, tool_call_id: str) -> Optional[ToolCallDB]:
        with Session(_engine()) as session:
            return session.get(ToolCallDB, tool_call_id)

    def get_by_session_id(self, session_id: str) -> List[ToolCallDB]:
        with Session(_engine()) as session:
            statement = (
                select(ToolCallDB)
                .where(ToolCallDB.session_id == session_id)
                .order_by(ToolCallDB.created_at.desc())
            )
            return session.exec(statement).all()

    def save(self, tool_call: ToolCallDB) -> ToolCallDB:
        with Session(_engine()) as session:
            merged = session.merge(tool_call)
            session.commit()
            session.refresh(merged)
            return merged


class PolicySnapshotRepository:
    def get_by_id(self, snapshot_id: str) -> Optional[PolicySnapshotDB]:
        with Session(_engine()) as session:
            return session.get(PolicySnapshotDB, snapshot_id)

    def get_by_session_id(self, session_id: str) -> Optional[PolicySnapshotDB]:
        with Session(_engine()) as session:
            statement = (
                select(PolicySnapshotDB)
                .where(PolicySnapshotDB.session_id == session_id)
                .order_by(PolicySnapshotDB.created_at.desc())
            )
            return session.exec(statement).first()

    def save(self, snapshot: PolicySnapshotDB) -> PolicySnapshotDB:
        with Session(_engine()) as session:
            merged = session.merge(snapshot)
            session.commit()
            session.refresh(merged)
            return merged
