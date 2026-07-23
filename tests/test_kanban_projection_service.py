from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

import agent.db_models  # noqa: F401
from ananta_contracts.kanban import CommentCardCommand, CreateCardCommand, MoveCardCommand
from agent.db_models.tasks import TaskDB
from agent.repositories.kanban_projection import SqlKanbanProjectionStore
from agent.services.kanban_authorization_service import KanbanPrincipal
from agent.services.kanban_projection_service import KanbanProjectionService, KanbanServiceError


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

