from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.source_control import (
    SourceControlBulkTargetCheckpointDB,
    SourceControlOperationDB,
)
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
    SourceControlApiRuntimeError,
)


def _store(tmp_path, **store_kwargs):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'source-control-idempotency.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SourceControlOperationDB.__table__.create(engine)
    SourceControlBulkTargetCheckpointDB.__table__.create(engine)
    return SQLSourceControlOperationStore(engine, **store_kwargs)


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


def test_released_claim_can_be_reclaimed_immediately(tmp_path) -> None:
    store = _store(tmp_path)
    first = store.claim(
        idempotency_key="operation_retry_example",
        plan_digest="a" * 64,
    )

    assert first.claim_token is not None
    store.release(
        idempotency_key="operation_retry_example",
        plan_digest="a" * 64,
        claim_token=first.claim_token,
    )
    reclaimed = store.claim(
        idempotency_key="operation_retry_example",
        plan_digest="a" * 64,
    )

    assert reclaimed.state == "claimed"
    assert reclaimed.claim_token != first.claim_token


def test_expired_claim_completion_is_fenced_by_token_reclaim(tmp_path) -> None:
    now = [100.0]
    store = _store(
        tmp_path,
        clock=lambda: now[0],
        lease_seconds=5.0,
    )
    late = store.claim(
        idempotency_key="operation_late_completion_example",
        plan_digest="a" * 64,
    )
    assert late.claim_token is not None

    now[0] = 106.0
    store.complete(
        idempotency_key="operation_late_completion_example",
        plan_digest="a" * 64,
        claim_token=late.claim_token,
        result={"status": "completed"},
    )
    replay = store.claim(
        idempotency_key="operation_late_completion_example",
        plan_digest="a" * 64,
    )
    assert replay.state == "completed"

    original = store.claim(
        idempotency_key="operation_reclaimed_example",
        plan_digest="b" * 64,
    )
    assert original.claim_token is not None
    now[0] = 112.0
    replacement = store.claim(
        idempotency_key="operation_reclaimed_example",
        plan_digest="b" * 64,
    )
    assert replacement.claim_token is not None
    assert replacement.claim_token != original.claim_token

    with pytest.raises(
        SourceControlApiRuntimeError,
        match="idempotency_completion_conflict",
    ):
        store.complete(
            idempotency_key="operation_reclaimed_example",
            plan_digest="b" * 64,
            claim_token=original.claim_token,
            result={"status": "stale"},
        )
    store.complete(
        idempotency_key="operation_reclaimed_example",
        plan_digest="b" * 64,
        claim_token=replacement.claim_token,
        result={"status": "completed"},
    )
