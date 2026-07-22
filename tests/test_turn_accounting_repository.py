import json

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session, select

from agent.db_models.turn_accounting import (
    TurnAccountingLedgerDB,
    TurnAccountingSourceCursorDB,
)
from agent.repositories.turn_accounting_repository import SqlTurnAccountingRepository
from agent.services.turn_accounting_service import (
    TurnAccountingCounters,
    TurnAccountingError,
    TurnAccountingEvent,
    TurnAccountingService,
)


def _event(sequence=1, counters=None, **changes):
    values = dict(
        event_id=f"event-{sequence}",
        credential_id="credential-secret",
        tenant_ref="tenant-a",
        turn_pool_ref="pool-a",
        room_ref="room-a",
        allocation_ref="allocation-secret",
        receiver_class="relay_required",
        sfu_node_ref="node-a",
        turn_runtime_epoch="runtime-1",
        sequence=sequence,
        observed_at_seconds=1020,
        window_started_at_seconds=1020,
        counters=counters or TurnAccountingCounters(1, 2, 100, 200, 3, 4, 0, 0),
    )
    values.update(changes)
    return TurnAccountingEvent(**values)


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'turn-accounting.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TurnAccountingLedgerDB.__table__,
            TurnAccountingSourceCursorDB.__table__,
        ],
    )
    return engine


def test_sql_accounting_survives_restart_and_fences_replay_gap_and_regression(tmp_path):
    engine = _engine(tmp_path)
    first = TurnAccountingService(
        SqlTurnAccountingRepository(db_engine=engine),
        pseudonym_secret=b"a" * 32,
        clock=lambda: 1040,
    )
    assert first.ingest(_event()).accepted

    restarted = TurnAccountingService(
        SqlTurnAccountingRepository(db_engine=engine),
        pseudonym_secret=b"a" * 32,
        clock=lambda: 1400,
    )
    assert restarted.ingest(_event()).replayed
    gap = restarted.ingest(
        _event(3, TurnAccountingCounters(2, 3, 50, 100, 4, 5, 1, 1))
    )
    assert "turn_accounting_sequence_gap_estimated" in gap.record.reason_codes
    assert "turn_accounting_counter_regression_estimated" in gap.record.reason_codes
    restart = restarted.ingest(
        _event(1, event_id="event-restart", turn_runtime_epoch="runtime-2")
    )
    assert "turn_accounting_runtime_restart_estimated" in restart.record.reason_codes
    with pytest.raises(TurnAccountingError, match="sequence_stale"):
        restarted.ingest(
            _event(1, event_id="event-stale", turn_runtime_epoch="runtime-2")
        )


def test_sql_accounting_is_scoped_content_free_paginated_and_purged(tmp_path):
    engine = _engine(tmp_path)
    clock = [1040]
    service = TurnAccountingService(
        SqlTurnAccountingRepository(db_engine=engine, purge_batch=10),
        pseudonym_secret=b"a" * 32,
        retention_seconds=120,
        late_window_seconds=60,
        clock=lambda: clock[0],
    )
    service.ingest(_event())
    service.ingest(_event(2, event_id="event-2"))

    page = service.page(tenant_ref="tenant-a", turn_pool_ref="pool-a", limit=1)
    second = service.page(
        tenant_ref="tenant-a",
        turn_pool_ref="pool-a",
        cursor=page.next_cursor,
        limit=1,
    )
    assert len(page.items) == len(second.items) == 1
    assert service.page(tenant_ref="tenant-b", turn_pool_ref="pool-a").items == ()
    with Session(engine) as db:
        row = db.exec(select(TurnAccountingLedgerDB)).first()
        encoded = json.dumps(row.model_dump(), sort_keys=True)
    for original in (
        "credential-secret",
        "tenant-a",
        "pool-a",
        "room-a",
        "allocation-secret",
        "node-a",
    ):
        assert original not in encoded

    clock[0] = 1160
    assert service.purge_expired(limit=10) == 3
    assert service.page(tenant_ref="tenant-a", turn_pool_ref="pool-a").items == ()
