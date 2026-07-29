"""Persistence adapters for Task-adjacent session and archive records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from agent.db_models import (
        AgentSessionDB,
        ArchivedTaskDB,
        PolicySnapshotDB,
        ToolCallDB,
    )


@dataclass(frozen=True)
class TaskAuxiliaryRepositoryDependencies:
    """Database primitives supplied dynamically by ``repositories.tasks``."""

    session_factory: Callable[[Any], Any]
    select: Callable[..., Any]
    delete: Callable[..., Any]
    archived_task_model: type
    agent_session_model: type
    tool_call_model: type
    policy_snapshot_model: type


class _EngineBoundRepository:
    """Small base that keeps engine ownership at the composition boundary."""

    def __init__(
        self,
        engine_provider: Callable[[], Any],
        dependencies_provider: Callable[
            [],
            TaskAuxiliaryRepositoryDependencies,
        ],
    ) -> None:
        self._engine_provider = engine_provider
        self._dependencies_provider = dependencies_provider

    def _engine(self) -> Any:
        return self._engine_provider()

    def _dependencies(self) -> TaskAuxiliaryRepositoryDependencies:
        return self._dependencies_provider()


class ArchivedTaskRepositoryMixin(_EngineBoundRepository):
    def get_all(self, limit: int = 100, offset: int = 0):
        dependencies = self._dependencies()
        model = dependencies.archived_task_model
        with dependencies.session_factory(self._engine()) as session:
            statement = dependencies.select(model).order_by(model.archived_at.desc()).offset(offset).limit(limit)
            return session.exec(statement).all()

    def get_by_id(
        self,
        task_id: str,
    ) -> Optional[ArchivedTaskDB]:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            return session.get(dependencies.archived_task_model, task_id)

    def save(self, task: ArchivedTaskDB):
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def delete(self, task_id: str):
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            task = session.get(dependencies.archived_task_model, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def delete_old(self, cutoff: float):
        dependencies = self._dependencies()
        model = dependencies.archived_task_model
        with dependencies.session_factory(self._engine()) as session:
            statement = dependencies.delete(model).where(model.archived_at < cutoff)
            session.exec(statement)
            session.commit()


class AgentSessionRepositoryMixin(_EngineBoundRepository):
    def get_all(self) -> List[AgentSessionDB]:
        dependencies = self._dependencies()
        model = dependencies.agent_session_model
        with dependencies.session_factory(self._engine()) as session:
            statement = dependencies.select(model).order_by(model.updated_at.desc())
            return session.exec(statement).all()

    def get_by_id(
        self,
        session_id: str,
    ) -> Optional[AgentSessionDB]:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            return session.get(
                dependencies.agent_session_model,
                session_id,
            )

    def get_by_task_id(
        self,
        task_id: str,
    ) -> List[AgentSessionDB]:
        dependencies = self._dependencies()
        model = dependencies.agent_session_model
        with dependencies.session_factory(self._engine()) as session:
            statement = dependencies.select(model).where(model.task_id == task_id).order_by(model.updated_at.desc())
            return session.exec(statement).all()

    def save(
        self,
        agent_session: AgentSessionDB,
    ) -> AgentSessionDB:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            merged = session.merge(agent_session)
            session.commit()
            session.refresh(merged)
            return merged


class ToolCallRepositoryMixin(_EngineBoundRepository):
    def get_by_id(
        self,
        tool_call_id: str,
    ) -> Optional[ToolCallDB]:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            return session.get(
                dependencies.tool_call_model,
                tool_call_id,
            )

    def get_by_session_id(
        self,
        session_id: str,
    ) -> List[ToolCallDB]:
        dependencies = self._dependencies()
        model = dependencies.tool_call_model
        with dependencies.session_factory(self._engine()) as session:
            statement = (
                dependencies.select(model).where(model.session_id == session_id).order_by(model.created_at.desc())
            )
            return session.exec(statement).all()

    def save(self, tool_call: ToolCallDB) -> ToolCallDB:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            merged = session.merge(tool_call)
            session.commit()
            session.refresh(merged)
            return merged


class PolicySnapshotRepositoryMixin(_EngineBoundRepository):
    def get_by_id(
        self,
        snapshot_id: str,
    ) -> Optional[PolicySnapshotDB]:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            return session.get(
                dependencies.policy_snapshot_model,
                snapshot_id,
            )

    def get_by_session_id(
        self,
        session_id: str,
    ) -> Optional[PolicySnapshotDB]:
        dependencies = self._dependencies()
        model = dependencies.policy_snapshot_model
        with dependencies.session_factory(self._engine()) as session:
            statement = (
                dependencies.select(model).where(model.session_id == session_id).order_by(model.created_at.desc())
            )
            return session.exec(statement).first()

    def save(
        self,
        snapshot: PolicySnapshotDB,
    ) -> PolicySnapshotDB:
        dependencies = self._dependencies()
        with dependencies.session_factory(self._engine()) as session:
            merged = session.merge(snapshot)
            session.commit()
            session.refresh(merged)
            return merged
