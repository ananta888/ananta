import contextlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

import agent.db_models  # noqa: F401
from agent.db_models.tasks import TaskDB
from agent.repositories.kanban_projection import SqlKanbanProjectionStore
from agent.services.kanban_authorization_service import KanbanPrincipal
from agent.services.kanban_projection_service import KanbanProjectionService, KanbanServiceError
from agent.services.vector_index_task_ingress_policy import (
    RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
)
from ananta_contracts.kanban import CommentCardCommand, CreateCardCommand, MoveCardCommand


class _NoopEvents:
    def publish(self, **_kwargs) -> None:
        pass


def _service(engine):
    return KanbanProjectionService(
        store=SqlKanbanProjectionStore(engine), events=_NoopEvents()
    )


ADMIN = KanbanPrincipal(subject="admin", role="admin", is_admin=True)


def test_projection_commands_and_idempotency(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'kanban.db'}", poolclass=NullPool)
    SQLModel.metadata.create_all(engine)
    service = _service(engine)
    card = service.create_card(
        "hub", CreateCardCommand(title="Task", idempotency_key="create"), ADMIN
    )
    moved = service.move_card(
        card.id,
        MoveCardCommand(
            board_id="hub",
            expected_revision=card.revision,
            idempotency_key="move",
            column_id="in_progress",
            position=0,
        ),
        ADMIN,
    )
    replay = service.move_card(
        card.id,
        MoveCardCommand(
            board_id="hub",
            expected_revision=card.revision,
            idempotency_key="move",
            column_id="in_progress",
            position=0,
        ),
        ADMIN,
    )
    assert moved.status == "in_progress"
    assert replay.revision == moved.revision

    commented = service.comment_card(
        card.id,
        CommentCardCommand(
            board_id="hub",
            expected_revision=moved.revision,
            idempotency_key="comment",
            body="durable",
        ),
        ADMIN,
    )
    assert commented.comment_count == 1
    assert service.list_comments("hub", card.id, ADMIN).items[0].body == "durable"


def test_revision_check_is_atomic(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", poolclass=NullPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(TaskDB(id="race", title="Race", status="todo"))
        session.commit()

    def apply(key: str) -> str:
        try:
            _service(engine).comment_card(
                "race",
                CommentCardCommand(
                    board_id="hub",
                    expected_revision=0,
                    idempotency_key=key,
                    body=key,
                ),
                ADMIN,
            )
            return "ok"
        except KanbanServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, ("one", "two")))
    assert sorted(results) == ["kanban_revision_conflict", "ok"]


def test_status_postcommit_runs_after_kanban_mutation_lock_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'postcommit-lock.db'}",
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(engine)
    service = _service(engine)
    card = service.create_card(
        "hub",
        CreateCardCommand(
            title="Fence",
            idempotency_key="create-fence",
        ),
        ADMIN,
    )
    lock_state = {"held": False}
    postcommit_states: list[bool] = []

    class LockPort:
        @contextlib.contextmanager
        def mutation_locks(self, _task_ids):
            assert lock_state["held"] is False
            lock_state["held"] = True
            try:
                yield True
            finally:
                lock_state["held"] = False

    monkeypatch.setattr(
        "agent.services.task_mutation_lock_service.get_task_mutation_lock_port",
        lambda: LockPort(),
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.run_external_task_status_post_commit",
        lambda *_args, **_kwargs: postcommit_states.append(
            lock_state["held"]
        ),
    )
    service.move_card(
        card.id,
        MoveCardCommand(
            board_id="hub",
            expected_revision=card.revision,
            idempotency_key="move-fence",
            column_id="completed",
            position=0,
        ),
        ADMIN,
    )
    assert postcommit_states == [False]


def test_kanban_mutations_never_touch_reserved_vector_tasks(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'vector-boundary.db'}",
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="vector-card",
                title="Reserved",
                status="todo",
                task_kind="vector_index_operation",
                kanban_position=1024,
            )
        )
        session.commit()

    service = _service(engine)
    with pytest.raises(KanbanServiceError) as denied:
        service.move_card(
            "vector-card",
            MoveCardCommand(
                board_id="hub",
                expected_revision=0,
                idempotency_key="move-vector",
                column_id="in_progress",
                position=0,
            ),
            ADMIN,
        )
    assert denied.value.code == (
        RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    )
    assert denied.value.status_code == 403

    service.create_card(
        "hub",
        CreateCardCommand(
            title="Ordinary",
            position=0,
            idempotency_key="create-ordinary",
        ),
        ADMIN,
    )
    with Session(engine) as session:
        vector = session.get(TaskDB, "vector-card")
        assert vector is not None
        assert vector.status == "todo"
        assert vector.kanban_position == 1024
        assert vector.kanban_revision == 0
