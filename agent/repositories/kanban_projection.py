"""Atomic persistence adapter for the TaskDB-backed Kanban projection."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generic, Iterator, Protocol, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent import database as database_module
from agent.db_models import AgentInfoDB
from agent.db_models.planning import GoalDB
from agent.db_models.tasks import TaskDB
from agent.db_models.teams import TeamDB, TeamMemberDB


@dataclass(frozen=True)
class KanbanScope:
    kind: str
    scope_id: str | None = None

    @property
    def board_id(self) -> str:
        return "hub" if self.kind == "hub" else f"{self.kind}:{self.scope_id}"


class KanbanStoreError(RuntimeError):
    pass


class KanbanTaskNotFound(KanbanStoreError):
    pass


class KanbanRevisionConflict(KanbanStoreError):
    def __init__(self, current_revision: int):
        super().__init__("the card revision no longer matches")
        self.current_revision = current_revision


class KanbanIdempotencyConflict(KanbanStoreError):
    pass


T = TypeVar("T")


@dataclass
class KanbanMutationResult(Generic[T]):
    task: TaskDB
    payload: T | None
    replayed: bool


class KanbanProjectionStorePort(Protocol):
    def list_tasks(self, scope: KanbanScope) -> list[TaskDB]: ...
    def get_goal(self, goal_id: str) -> GoalDB | None: ...
    def get_team(self, team_id: str) -> TeamDB | None: ...


class SqlKanbanProjectionStore:
    """One unit of work per command; it never owns a second task queue."""

    def __init__(self, db_engine: Engine | None = None):
        self._engine = db_engine or database_module.engine
        if self._engine is None:
            raise RuntimeError("database engine is not initialized")

    @contextmanager
    def _transaction(self, scope: KanbanScope) -> Iterator[Session]:
        with Session(self._engine, expire_on_commit=False) as session:
            try:
                if self._engine.dialect.name == "sqlite":
                    session.exec(text("BEGIN IMMEDIATE"))
                elif self._engine.dialect.name == "postgresql":
                    session.exec(
                        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                        params={"scope": f"kanban:{scope.board_id}"},
                    )
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _statement(scope: KanbanScope):
        statement = select(TaskDB)
        if scope.kind == "goal":
            return statement.where(TaskDB.goal_id == scope.scope_id)
        if scope.kind == "team":
            return statement.where(TaskDB.team_id == scope.scope_id)
        return statement

    def _locked_tasks(self, session: Session, scope: KanbanScope) -> list[TaskDB]:
        statement = self._statement(scope)
        if self._engine.dialect.name != "sqlite":
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _idempotency_state(task: TaskDB, key_hash: str, digest: str) -> str | None:
        for event in reversed(list(task.history or [])):
            details = event.get("details") if isinstance(event, dict) else None
            if not isinstance(details, dict) or details.get("idempotency_key_hash") != key_hash:
                continue
            return "replay" if details.get("idempotency_digest") == digest else "conflict"
        return None

    def list_tasks(self, scope: KanbanScope) -> list[TaskDB]:
        with Session(self._engine, expire_on_commit=False) as session:
            return list(session.exec(self._statement(scope)).all())

    def get_goal(self, goal_id: str) -> GoalDB | None:
        with Session(self._engine, expire_on_commit=False) as session:
            return session.get(GoalDB, goal_id)

    def get_team(self, team_id: str) -> TeamDB | None:
        with Session(self._engine, expire_on_commit=False) as session:
            return session.get(TeamDB, team_id)

    def list_goals(self) -> list[GoalDB]:
        with Session(self._engine, expire_on_commit=False) as session:
            return list(session.exec(select(GoalDB)).all())

    def list_teams(self) -> list[TeamDB]:
        with Session(self._engine, expire_on_commit=False) as session:
            return list(session.exec(select(TeamDB)).all())

    def get_agent(self, agent_id: str) -> AgentInfoDB | None:
        with Session(self._engine, expire_on_commit=False) as session:
            return session.get(AgentInfoDB, agent_id)

    def is_team_member(self, team_id: str, agent_id: str) -> bool:
        with Session(self._engine) as session:
            statement = select(TeamMemberDB).where(
                TeamMemberDB.team_id == team_id,
                TeamMemberDB.agent_id == agent_id,
            )
            return session.exec(statement).first() is not None

    def create_task(
        self,
        scope: KanbanScope,
        task: TaskDB,
        *,
        key_hash: str,
        request_digest: str,
        prepare: Callable[[TaskDB, list[TaskDB]], T],
    ) -> KanbanMutationResult[T]:
        with self._transaction(scope) as session:
            tasks = self._locked_tasks(session, scope)
            existing = session.get(TaskDB, task.id)
            if existing is not None:
                state = self._idempotency_state(existing, key_hash, request_digest)
                if state == "replay":
                    return KanbanMutationResult(existing, None, True)
                raise KanbanIdempotencyConflict("idempotency key was reused")
            payload = prepare(task, tasks)
            session.add(task)
            session.flush()
            return KanbanMutationResult(task, payload, False)

    def mutate_task(
        self,
        scope: KanbanScope,
        task_id: str,
        *,
        expected_revision: int,
        key_hash: str,
        request_digest: str,
        mutate: Callable[[TaskDB, list[TaskDB]], T],
    ) -> KanbanMutationResult[T]:
        with self._transaction(scope) as session:
            tasks = self._locked_tasks(session, scope)
            task = next((item for item in tasks if item.id == task_id), None)
            if task is None:
                raise KanbanTaskNotFound("card was not found in the requested board")
            state = self._idempotency_state(task, key_hash, request_digest)
            if state == "replay":
                return KanbanMutationResult(task, None, True)
            if state == "conflict":
                raise KanbanIdempotencyConflict("idempotency key was reused")
            revision = int(task.kanban_revision or 0)
            if revision != expected_revision:
                raise KanbanRevisionConflict(revision)
            payload = mutate(task, tasks)
            session.flush()
            return KanbanMutationResult(task, payload, False)

