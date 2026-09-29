from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from agent.db_models import KnowledgeIndexWorkerDispatchReceiptDB
from worker.retrieval.knowledge_index_dispatch_receipt_repository import (
    SqlKnowledgeIndexWorkerDispatchReceiptRepository,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    KnowledgeIndexWorkerDispatchReceiptDB.__table__.create(engine)
    return engine


def _claim(**overrides):
    values = {
        "worker_id": "worker-index-01",
        "job_id": "knowledge-index-" + "a" * 32,
        "assignment_id": "assignment-alpha",
        "lease_id": "lease-alpha",
        "marker_digest": "b" * 64,
        "manifest_binding_digest": "c" * 64,
        "lease_expires_epoch_ms": 20_000,
        "grant_expires_at_epoch_ms": 20_000,
    }
    values.update(overrides)
    return values


def test_receipt_ledger_allows_same_job_once_per_worker() -> None:
    engine = _engine()
    ledger = SqlKnowledgeIndexWorkerDispatchReceiptRepository(
        db_engine=engine,
        clock_ms=lambda: 10_000,
    )

    first = ledger.claim(**_claim())
    second_worker = ledger.claim(**_claim(worker_id="worker-index-02"))

    assert first["worker_id"] == "worker-index-01"
    assert second_worker["worker_id"] == "worker-index-02"
    with Session(engine) as session:
        assert len(session.exec(select(KnowledgeIndexWorkerDispatchReceiptDB)).all()) == 2


@pytest.mark.parametrize(
    ("changed_binding", "reason_code"),
    [
        ({}, "knowledge_index_worker_dispatch_result_pending"),
        (
            {
                "assignment_id": "assignment-reissued",
                "lease_id": "lease-reissued",
                "marker_digest": "d" * 64,
            },
            "knowledge_index_worker_dispatch_binding_conflict",
        ),
    ],
)
def test_receipt_ledger_fails_closed_before_result_is_persisted(
    changed_binding,
    reason_code,
) -> None:
    engine = _engine()
    ledger = SqlKnowledgeIndexWorkerDispatchReceiptRepository(
        db_engine=engine,
        clock_ms=lambda: 10_000,
    )
    ledger.claim(**_claim())

    with pytest.raises(
        ValueError,
        match=reason_code,
    ):
        ledger.claim(**_claim(**changed_binding))


def test_receipt_ledger_parallel_claim_has_one_winner() -> None:
    engine = _engine()
    ledger = SqlKnowledgeIndexWorkerDispatchReceiptRepository(
        db_engine=engine,
        clock_ms=lambda: 10_000,
    )

    def invoke():
        try:
            return ledger.claim(**_claim())
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: invoke(), range(2)))

    assert len([value for value in outcomes if isinstance(value, dict)]) == 1
    assert [value for value in outcomes if isinstance(value, str)] == [
        "knowledge_index_worker_dispatch_result_pending"
    ]


def test_receipt_ledger_replays_exact_durable_terminal_result() -> None:
    engine = _engine()
    ledger = SqlKnowledgeIndexWorkerDispatchReceiptRepository(
        db_engine=engine,
        clock_ms=lambda: 10_000,
    )
    binding = _claim()
    claimed = ledger.claim(**binding)
    result_payload = {
        "schema": "example.result.v1",
        "status": "completed",
        "values": [1, 2, 3],
    }

    completed = ledger.complete(
        **{
            key: binding[key]
            for key in (
                "worker_id",
                "job_id",
                "assignment_id",
                "lease_id",
                "marker_digest",
                "manifest_binding_digest",
            )
        },
        result_payload=result_payload,
    )
    replayed = ledger.claim(**binding)

    assert claimed["state"] == "claimed"
    assert claimed["result_payload"] is None
    assert completed["state"] == "completed"
    assert completed["result_payload"] == result_payload
    assert completed["result_digest"] == replayed["result_digest"]
    assert replayed["result_payload"] == result_payload
    assert set(ledger.get_receipt(
        worker_id=binding["worker_id"],
        job_id=binding["job_id"],
    )) == {
        "schema",
        "job_id",
        "phase",
        "worker_id",
        "assignment_id",
        "lease_id",
        "marker_digest",
        "manifest_binding_digest",
        "claimed_at_epoch_ms",
    }


def test_receipt_ledger_rejects_different_result_or_binding() -> None:
    engine = _engine()
    ledger = SqlKnowledgeIndexWorkerDispatchReceiptRepository(
        db_engine=engine,
        clock_ms=lambda: 10_000,
    )
    binding = _claim()
    ledger.claim(**binding)
    completion_binding = {
        key: binding[key]
        for key in (
            "worker_id",
            "job_id",
            "assignment_id",
            "lease_id",
            "marker_digest",
            "manifest_binding_digest",
        )
    }
    ledger.complete(
        **completion_binding,
        result_payload={"status": "completed"},
    )

    with pytest.raises(
        ValueError,
        match="knowledge_index_worker_dispatch_result_conflict",
    ):
        ledger.complete(
            **completion_binding,
            result_payload={"status": "failed"},
        )
    with pytest.raises(
        ValueError,
        match="knowledge_index_worker_dispatch_binding_conflict",
    ):
        ledger.claim(
            **_claim(manifest_binding_digest="d" * 64)
        )


@pytest.mark.parametrize(
    ("lease_expiry", "grant_expiry", "reason_code"),
    [
        (10_000, 20_000, "knowledge_index_execution_lease_stale"),
        (
            20_000,
            10_000,
            "knowledge_index_source_access_grant_expired",
        ),
    ],
)
def test_receipt_ledger_rechecks_expiry_inside_atomic_claim(
    lease_expiry,
    grant_expiry,
    reason_code,
) -> None:
    engine = _engine()
    ledger = SqlKnowledgeIndexWorkerDispatchReceiptRepository(
        db_engine=engine,
        clock_ms=lambda: 10_000,
    )

    with pytest.raises(ValueError, match=reason_code):
        ledger.claim(
            **_claim(
                lease_expires_epoch_ms=lease_expiry,
                grant_expires_at_epoch_ms=grant_expiry,
            )
        )

    with Session(engine) as session:
        assert session.exec(select(KnowledgeIndexWorkerDispatchReceiptDB)).all() == []
