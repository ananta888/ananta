from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import (
    SemanticMediaAuditEventDB,
    SemanticMediaAuditOutboxDB,
    SemanticSfuRoomStateDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditService,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def db_engine():
    configured = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(configured)
    return configured


def _event(*, now_ms: int = 2_000_000, key: str = "atomic-command-a"):
    service = SemanticMediaAuditService(
        InMemorySemanticMediaAuditRepository(),
        clock_ms=lambda: now_ms,
    )
    return service.prepare_transition(
        idempotency_key=key,
        tenant_digest=_digest("tenant-a"),
        scope_digest=_digest("session-a"),
        event_type="semantic_admission",
        transition="joined",
        reason_code="hub_confirmed",
        epoch=3,
        contract_ref=_digest("room-a"),
        retention_ms=3_600_000,
    )


def _domain_row() -> SemanticSfuRoomStateDB:
    return SemanticSfuRoomStateDB(
        id="room-state-a",
        tenant_id="tenant-a",
        session_id="session-a",
        revision=1,
        participants={"actor-a": 3},
        publications={},
        subscriptions={},
    )


def test_domain_mutation_and_audit_command_commit_or_rollback_together(db_engine) -> None:
    with pytest.raises(RuntimeError, match="inject rollback"):
        with Session(db_engine) as db:
            db.add(_domain_row())
            SqlSemanticMediaAuditOutbox.enqueue_in_session(db, _event())
            raise RuntimeError("inject rollback")

    with Session(db_engine) as db:
        assert db.get(SemanticSfuRoomStateDB, "room-state-a") is None
        assert db.exec(select(SemanticMediaAuditOutboxDB)).all() == []

    with Session(db_engine) as db:
        db.add(_domain_row())
        assert SqlSemanticMediaAuditOutbox.enqueue_in_session(db, _event()) is True
        db.commit()

    with Session(db_engine) as db:
        assert db.get(SemanticSfuRoomStateDB, "room-state-a") is not None
        assert len(db.exec(select(SemanticMediaAuditOutboxDB)).all()) == 1


def test_dispatch_is_exactly_once_and_retry_safe(db_engine) -> None:
    first = _event(now_ms=2_000_000)
    retry = _event(now_ms=2_000_999)
    assert retry.event_id == first.event_id
    with Session(db_engine) as db:
        SqlSemanticMediaAuditOutbox.enqueue_in_session(db, first)
        db.commit()

    outbox = SqlSemanticMediaAuditOutbox(db_engine=db_engine, clock_ms=lambda: 3_000_000)
    delivered = outbox.dispatch_pending(limit=10)
    assert (delivered.delivered, delivered.replayed, delivered.failed, delivered.pending) == (1, 0, 0, 0)
    empty = outbox.dispatch_pending(limit=10)
    assert (empty.attempted, empty.pending) == (0, 0)
    with Session(db_engine) as db:
        rows = db.exec(select(SemanticMediaAuditEventDB)).all()
        assert len(rows) == 1
        assert rows[0].id == first.event_id
        assert rows[0].created_at_ms == first.created_at_ms
        assert SqlSemanticMediaAuditOutbox.enqueue_in_session(db, retry) is False
        db.commit()
    assert outbox.pending_count() == 0


def test_sink_failure_leaves_recoverable_pending_command(db_engine) -> None:
    with Session(db_engine) as db:
        db.add(_domain_row())
        SqlSemanticMediaAuditOutbox.enqueue_in_session(db, _event())
        db.commit()
    with db_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_audit_insert BEFORE INSERT ON semantic_media_audit_events "
            "BEGIN SELECT RAISE(FAIL, 'audit sink unavailable'); END"
        )

    outbox = SqlSemanticMediaAuditOutbox(db_engine=db_engine, clock_ms=lambda: 3_000_000)
    failed = outbox.dispatch_pending(limit=10)
    assert (failed.failed, failed.pending) == (1, 1)
    with Session(db_engine) as db:
        assert db.get(SemanticSfuRoomStateDB, "room-state-a") is not None
        assert db.exec(select(SemanticMediaAuditEventDB)).all() == []

    with db_engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER reject_audit_insert")
    recovered = outbox.dispatch_pending(limit=10)
    assert (recovered.delivered, recovered.failed, recovered.pending) == (1, 0, 0)


def test_missing_sink_table_does_not_lose_pending_command(db_engine) -> None:
    with Session(db_engine) as db:
        SqlSemanticMediaAuditOutbox.enqueue_in_session(db, _event())
        db.commit()
    SemanticMediaAuditEventDB.__table__.drop(db_engine)

    outbox = SqlSemanticMediaAuditOutbox(db_engine=db_engine, clock_ms=lambda: 3_000_000)
    failed = outbox.dispatch_pending(limit=10)
    assert (failed.failed, failed.pending) == (1, 1)

    SemanticMediaAuditEventDB.__table__.create(db_engine)
    recovered = outbox.dispatch_pending(limit=10)
    assert (recovered.delivered, recovered.failed, recovered.pending) == (1, 0, 0)


def test_outbox_has_closed_content_free_schema(db_engine) -> None:
    with Session(db_engine) as db:
        SqlSemanticMediaAuditOutbox.enqueue_in_session(db, _event())
        db.commit()
        row = db.exec(select(SemanticMediaAuditOutboxDB)).one()
        projection = {
            column.name: getattr(row, column.name)
            for column in SemanticMediaAuditOutboxDB.__table__.columns
        }
    forbidden = {
        "payload",
        "audio",
        "transcript",
        "feature",
        "secret",
        "token",
        "credential",
        "tenant_id",
        "subject",
    }
    assert not (forbidden & set(projection))
    assert not any(marker in repr(projection).casefold() for marker in forbidden)
