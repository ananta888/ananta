from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.source_control import SourceControlOperationDB
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
    SourceControlApiRuntimeError,
)


def _store(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'source-control-idempotency.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SourceControlOperationDB.__table__.create(engine)
    return SQLSourceControlOperationStore(engine)


def test_parallel_claim_allows_exactly_one_mutation_owner(tmp_path) -> None:
    store = _store(tmp_path)
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        return store.claim(
            idempotency_key="bulk_parallel_example",
            plan_digest="a" * 64,
        ).state

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: claim(), range(2)))

    assert sorted(states) == ["claimed", "in_progress"]


def test_completed_claim_replays_and_rejects_key_reuse(tmp_path) -> None:
    store = _store(tmp_path)
    first = store.claim(
        idempotency_key="bulk_replay_example",
        plan_digest="a" * 64,
    )
    store.complete(
        idempotency_key="bulk_replay_example",
        plan_digest="a" * 64,
        result={"results": [{"status": "accepted"}]},
    )
    replay = store.claim(
        idempotency_key="bulk_replay_example",
        plan_digest="a" * 64,
    )

    assert first.state == "claimed"
    assert replay.state == "completed"
    assert replay.result == {"results": [{"status": "accepted"}]}
    with pytest.raises(
        SourceControlApiRuntimeError,
        match="idempotency_key_conflict",
    ):
        store.claim(
            idempotency_key="bulk_replay_example",
            plan_digest="b" * 64,
        )
