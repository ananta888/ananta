"""Atomic persistence adapter for the TaskDB-backed Kanban projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Iterator, Protocol, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent import database as database_module
from agent.db_models import AgentInfoDB
from agent.db_models.kanban_projection import (
    KanbanBoardSequenceDB,
    KanbanOutboxEventDB,
)
from agent.db_models.planning import GoalDB
from agent.db_models.tasks import TaskDB
from agent.db_models.teams import TeamDB, TeamMemberDB
from ananta_contracts.kanban_events import (
    KanbanEvent,
    KanbanEventGapReason,
)


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
    event: KanbanEvent | None = None


@dataclass(frozen=True)
class KanbanProjectionSnapshot:
    tasks: tuple[TaskDB, ...]
    goal: GoalDB | None
    team: TeamDB | None
    event_sequence: int


@dataclass(frozen=True)
class KanbanOutboxRead:
    events: tuple[KanbanEvent, ...]
    latest_sequence: int
    has_more: bool
    gap_reason: KanbanEventGapReason | None = None


KanbanEventFactory = Callable[[TaskDB, int], KanbanEvent]


class KanbanProjectionStorePort(Protocol):
    def list_tasks(self, scope: KanbanScope) -> list[TaskDB]: ...
    def get_goal(self, goal_id: str) -> GoalDB | None: ...
    def get_team(self, team_id: str) -> TeamDB | None: ...
    def read_snapshot(self, scope: KanbanScope) -> KanbanProjectionSnapshot: ...
    def read_events(
        self,
        scope: KanbanScope,
        *,
        after_sequence: int,
        limit: int,
    ) -> KanbanOutboxRead: ...


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

    @contextmanager
    def _read_transaction(self, scope: KanbanScope) -> Iterator[Session]:
        """Provide one database snapshot while coordinating with Kanban writers."""

        with Session(self._engine, expire_on_commit=False) as session:
            try:
                if self._engine.dialect.name == "sqlite":
                    session.exec(text("BEGIN"))
                elif self._engine.dialect.name == "postgresql":
                    session.exec(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    )
                    session.exec(
                        text(
                            "SELECT pg_advisory_xact_lock_shared("
                            "hashtext(:scope))"
                        ),
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

    @staticmethod
    def _event_dedupe_key(event: KanbanEvent) -> str:
        canonical = json.dumps(
            [
                event.board_id,
                event.task_id,
                event.revision,
                event.event_type,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_from_row(row: KanbanOutboxEventDB) -> KanbanEvent:
        occurred_at = row.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        return KanbanEvent(
            event_id=row.event_id,
            board_id=row.board_id,
            task_id=row.task_id,
            revision=row.revision,
            sequence=row.sequence,
            event_type=row.event_type,
            occurred_at=occurred_at,
            payload=dict(row.payload or {}),
        )

    def _append_event(
        self,
        session: Session,
        scope: KanbanScope,
        task: TaskDB,
        event_factory: KanbanEventFactory | None,
    ) -> KanbanEvent | None:
        if event_factory is None:
            return None
        counter = session.get(KanbanBoardSequenceDB, scope.board_id)
        if counter is None:
            counter = KanbanBoardSequenceDB(board_id=scope.board_id)
            session.add(counter)
            session.flush()
        counter.last_sequence = int(counter.last_sequence or 0) + 1
        counter.updated_at = datetime.now(tz=timezone.utc)
        event = event_factory(task, counter.last_sequence)
        if event.board_id != scope.board_id:
            raise KanbanStoreError("outbox event board does not match transaction scope")
        if event.sequence != counter.last_sequence:
            raise KanbanStoreError("outbox event sequence does not match committed cursor")
        session.add(
            KanbanOutboxEventDB(
                board_id=event.board_id,
                sequence=event.sequence,
                event_id=event.event_id,
                task_id=event.task_id,
                revision=event.revision,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                payload=dict(event.payload),
                dedupe_key=self._event_dedupe_key(event),
            )
        )
        session.flush()
        return event

    def read_snapshot(self, scope: KanbanScope) -> KanbanProjectionSnapshot:
        with self._read_transaction(scope) as session:
            tasks = tuple(session.exec(self._statement(scope)).all())
            goal = (
                session.get(GoalDB, scope.scope_id)
                if scope.kind == "goal" and scope.scope_id
                else None
            )
            team = (
                session.get(TeamDB, scope.scope_id)
                if scope.kind == "team" and scope.scope_id
                else None
            )
            counter = session.get(KanbanBoardSequenceDB, scope.board_id)
            return KanbanProjectionSnapshot(
                tasks=tasks,
                goal=goal,
                team=team,
                event_sequence=(
                    int(counter.last_sequence or 0) if counter is not None else 0
                ),
            )

    def read_events(
        self,
        scope: KanbanScope,
        *,
        after_sequence: int,
        limit: int,
    ) -> KanbanOutboxRead:
        if after_sequence < 0 or limit < 1:
            raise ValueError("kanban_event_cursor_invalid")
        with self._read_transaction(scope) as session:
            counter = session.get(KanbanBoardSequenceDB, scope.board_id)
            latest = (
                int(counter.last_sequence or 0) if counter is not None else 0
            )
            if after_sequence > latest:
                return KanbanOutboxRead(
                    events=(),
                    latest_sequence=latest,
                    has_more=False,
                    gap_reason=KanbanEventGapReason.CLIENT_SEQUENCE_AHEAD,
                )
            rows = tuple(
                session.exec(
                    select(KanbanOutboxEventDB)
                    .where(
                        KanbanOutboxEventDB.board_id == scope.board_id,
                        KanbanOutboxEventDB.sequence > after_sequence,
                    )
                    .order_by(KanbanOutboxEventDB.sequence)
                    .limit(limit + 1)
                ).all()
            )
            expected = after_sequence + 1
            for row in rows:
                if row.sequence != expected:
                    return KanbanOutboxRead(
                        events=(),
                        latest_sequence=latest,
                        has_more=False,
                        gap_reason=KanbanEventGapReason.SEQUENCE_GAP,
                    )
                expected += 1
            if after_sequence < latest and not rows:
                return KanbanOutboxRead(
                    events=(),
                    latest_sequence=latest,
                    has_more=False,
                    gap_reason=KanbanEventGapReason.SEQUENCE_GAP,
                )
            selected = rows[:limit]
            return KanbanOutboxRead(
                events=tuple(self._event_from_row(row) for row in selected),
                latest_sequence=latest,
                has_more=len(rows) > len(selected),
            )

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
        event_factory: KanbanEventFactory | None = None,
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
            event = self._append_event(session, scope, task, event_factory)
            return KanbanMutationResult(task, payload, False, event)

    def mutate_task(
        self,
        scope: KanbanScope,
        task_id: str,
        *,
        expected_revision: int,
        key_hash: str,
        request_digest: str,
        mutate: Callable[[TaskDB, list[TaskDB]], T],
        event_factory: KanbanEventFactory | None = None,
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
            event = self._append_event(session, scope, task, event_factory)
            return KanbanMutationResult(task, payload, False, event)
