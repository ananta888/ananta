from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, delete

import agent.db_models  # noqa: F401
from agent.db_models.kanban_projection import KanbanOutboxEventDB
from agent.repositories.kanban_projection import (
    KanbanScope,
    SqlKanbanProjectionStore,
)
from agent.services.kanban_authorization_service import KanbanPrincipal
from agent.services.kanban_event_stream_service import (
    KanbanEventReconnectService,
    SqlKanbanEventJournal,
)
from agent.services.kanban_projection_service import KanbanProjectionService
from ananta_contracts.kanban import CommentCardCommand, CreateCardCommand


ADMIN = KanbanPrincipal(subject="admin", role="admin", is_admin=True)


class _NoopEvents:
    def publish(self, **_kwargs) -> None:
        pass


class _FailingMirror:
    def __init__(self) -> None:
        self.calls = 0

    def mirror(self, _event) -> None:
        self.calls += 1
        raise RuntimeError("injected mirror crash")


def _engine(path: Path):
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    SQLModel.metadata.create_all(engine)
    return engine


def _service(engine, *, mirror=None) -> KanbanProjectionService:
    return KanbanProjectionService(
        store=SqlKanbanProjectionStore(engine),
        events=_NoopEvents(),
        event_mirror=mirror,
    )


def _create(service: KanbanProjectionService, key: str, title: str):
    return service.create_card(
        "hub",
        CreateCardCommand(
            title=title,
            idempotency_key=key,
        ),
        ADMIN,
    )


def test_outbox_is_atomic_ordered_and_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "ordered.db")
    store = SqlKanbanProjectionStore(engine)
    service = _service(engine)

    first = _create(service, "first", "First")
    replayed = _create(service, "first", "First")
    second = _create(service, "second", "Second")
    events = store.read_events(
        KanbanScope("hub"),
        after_sequence=0,
        limit=100,
    )

    assert replayed.id == first.id
    assert [event.sequence for event in events.events] == [1, 2]
    assert [event.task_id for event in events.events] == [first.id, second.id]
    assert events.latest_sequence == 2
    assert events.has_more is False

    snapshot = service.get_snapshot("hub", ADMIN)
    assert snapshot.schema_version == "kanban.snapshot.v1"
    assert snapshot.event_sequence == 2
    assert snapshot.board.card_count == 2
    assert {card.id for card in snapshot.cards} == {first.id, second.id}


def test_concurrent_writers_allocate_one_monotonic_board_sequence(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path / "concurrent.db")
    service = _service(engine)
    cards = [_create(service, f"create-{index}", f"Card {index}") for index in range(8)]

    def comment(index: int) -> int:
        card = cards[index]
        updated = _service(engine).comment_card(
            card.id,
            CommentCardCommand(
                board_id="hub",
                expected_revision=card.revision,
                idempotency_key=f"comment-{index}",
                body=f"Comment {index}",
            ),
            ADMIN,
        )
        return updated.revision

    with ThreadPoolExecutor(max_workers=8) as pool:
        revisions = list(pool.map(comment, range(len(cards))))

    replay = SqlKanbanProjectionStore(engine).read_events(
        KanbanScope("hub"),
        after_sequence=0,
        limit=100,
    )
    snapshot = _service(engine).get_snapshot("hub", ADMIN)

    assert revisions == [2] * len(cards)
    assert [event.sequence for event in replay.events] == list(range(1, 17))
    assert len({event.sequence for event in replay.events}) == 16
    assert snapshot.event_sequence == 16
    assert sum(card.revision for card in snapshot.cards) == 16


def test_mirror_crash_cannot_lose_committed_event(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "crash-window.db")
    mirror = _FailingMirror()
    card = _create(_service(engine, mirror=mirror), "crash", "Durable")

    replay = KanbanEventReconnectService(
        SqlKanbanEventJournal(
            lambda: SqlKanbanProjectionStore(engine)
        )
    ).reconnect(board_id="hub", after_sequence=0, limit=100)

    assert mirror.calls == 1
    assert replay.gap_detected is False
    assert [event.task_id for event in replay.events] == [card.id]
    assert replay.latest_sequence == 1


def test_durable_replay_detects_corrupt_sequence_gap(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "gap.db")
    service = _service(engine)
    _create(service, "one", "One")
    _create(service, "two", "Two")
    with Session(engine) as session:
        session.exec(
            delete(KanbanOutboxEventDB).where(
                KanbanOutboxEventDB.board_id == "hub",
                KanbanOutboxEventDB.sequence == 1,
            )
        )
        session.commit()

    replay = KanbanEventReconnectService(
        SqlKanbanEventJournal(
            lambda: SqlKanbanProjectionStore(engine)
        )
    ).reconnect(board_id="hub", after_sequence=0, limit=100)

    assert replay.events == ()
    assert replay.gap_detected is True
    assert replay.gap_reason == "sequence_gap"
    assert replay.snapshot_required is True
    assert replay.snapshot_url == "/api/v1/kanban/boards/hub/snapshot"

