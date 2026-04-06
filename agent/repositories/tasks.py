from typing import List, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import ArchivedTaskDB, TaskDB


class TaskRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(TaskDB)).all()

    def get_by_id(self, task_id: str) -> Optional[TaskDB]:
        with Session(engine) as session:
            return session.get(TaskDB, task_id)

    def get_by_goal_id(self, goal_id: str) -> List[TaskDB]:
        with Session(engine) as session:
            return session.exec(select(TaskDB).where(TaskDB.goal_id == goal_id)).all()

    def save(self, task: TaskDB):
        with Session(engine) as session:
            task = session.merge(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        with Session(engine) as session:
            task = session.get(TaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def clear_team_assignments(self, team_id: str) -> int:
        with Session(engine) as session:
            statement = select(TaskDB).where(TaskDB.team_id == team_id)
            tasks = session.exec(statement).all()
            for task in tasks:
                task.team_id = None
                session.add(task)
            session.commit()
            return len(tasks)

    def get_old_tasks(self, cutoff: float):
        with Session(engine) as session:
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
        with Session(engine) as session:
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
        with Session(engine) as session:
            statement = select(ArchivedTaskDB).order_by(ArchivedTaskDB.archived_at.desc()).offset(offset).limit(limit)
            return session.exec(statement).all()

    def get_by_id(self, task_id: str) -> Optional[ArchivedTaskDB]:
        with Session(engine) as session:
            return session.get(ArchivedTaskDB, task_id)

    def save(self, task: ArchivedTaskDB):
        with Session(engine) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        with Session(engine) as session:
            task = session.get(ArchivedTaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def delete_old(self, cutoff: float):
        with Session(engine) as session:
            from sqlmodel import delete

            statement = delete(ArchivedTaskDB).where(ArchivedTaskDB.archived_at < cutoff)
            session.exec(statement)
            session.commit()
