from __future__ import annotations

import concurrent.futures

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.semantic_lease_repository import (
    LeaseRequest,
    SemanticLeaseRepository,
    SemanticLeaseRepositoryError,
)


def lease_request(now: float, **values) -> LeaseRequest:
    defaults = dict(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        contract_id="contract-a",
        contract_digest="a" * 64,
        session_id="session-a",
        epoch=1,
        task_type="visual_extract",
        audience="viewer-a",
        role="primary",
        executor_id="worker-a",
        sequence_start=0,
        sequence_end=10,
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        ttl_seconds=30.0,
        deadline_at=now + 60.0,
    )
    defaults.update(values)
    return LeaseRequest(**defaults)


def store(now_ref):
    db = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db)
    return SemanticLeaseRepository(db_engine=db, clock=lambda: now_ref[0], clock_skew_seconds=0), db


def test_one_winner_from_one_hundred_concurrent_acquires() -> None:
    now = [1_000.0]
    repo, _ = store(now)

    def acquire(index: int) -> str:
        try:
            return repo.acquire(lease_request(now[0], executor_id=f"worker-{index}")).id
        except SemanticLeaseRepositoryError as exc:
            return exc.reason_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(acquire, range(100)))
    assert len([item for item in results if item.startswith("semantic-lease-")]) == 1
    assert results.count("lease_overlap") == 99


def test_fencing_is_monotonic_across_restart_expiry_and_revocation() -> None:
    now = [1_000.0]
    repo, db = store(now)
    first = repo.acquire(lease_request(now[0], ttl_seconds=10))
    revoked = repo.revoke(lease_id=first.id, fencing_token=first.fencing_token, expected_version=first.version)
    assert revoked.status == "revoked"
    restarted = SemanticLeaseRepository(db_engine=db, clock=lambda: now[0], clock_skew_seconds=0)
    second = restarted.acquire(lease_request(now[0], executor_id="worker-b"))
    assert second.fencing_token > first.fencing_token
    with pytest.raises(SemanticLeaseRepositoryError, match="lease_not_authorized"):
        restarted.authorize_result(
            lease_id=first.id,
            contract_digest="a" * 64,
            fencing_token=first.fencing_token,
            session_id="session-a",
            epoch=1,
            task_type="visual_extract",
            audience="viewer-a",
        )
    now[0] += 31
    with pytest.raises(SemanticLeaseRepositoryError, match="lease_not_authorized"):
        restarted.authorize_result(
            lease_id=second.id,
            contract_digest="a" * 64,
            fencing_token=second.fencing_token,
            session_id="session-a",
            epoch=1,
            task_type="visual_extract",
            audience="viewer-a",
        )


def test_partial_sequence_overlap_is_rejected_and_cas_conflicts() -> None:
    now = [1_000.0]
    repo, _ = store(now)
    lease = repo.acquire(lease_request(now[0], sequence_start=10, sequence_end=20))
    with pytest.raises(SemanticLeaseRepositoryError, match="lease_overlap"):
        repo.acquire(lease_request(now[0], sequence_start=20, sequence_end=30))
    with pytest.raises(SemanticLeaseRepositoryError, match="lease_binding_mismatch"):
        repo.authorize_result(
            lease_id=lease.id,
            contract_digest="a" * 64,
            fencing_token=lease.fencing_token + 1,
            session_id="session-a",
            epoch=1,
            task_type="visual_extract",
            audience="viewer-a",
        )
    renewed = repo.renew(
        lease_id=lease.id, fencing_token=lease.fencing_token, expected_version=lease.version, ttl_seconds=20
    )
    with pytest.raises(SemanticLeaseRepositoryError, match="lease_cas_conflict"):
        repo.renew(lease_id=lease.id, fencing_token=lease.fencing_token, expected_version=lease.version, ttl_seconds=20)
    assert renewed.version == lease.version + 1


def test_validator_scopes_allow_independent_executors_but_fence_each_executor() -> None:
    now = [1_000.0]
    repo, _ = store(now)
    first = repo.acquire(
        lease_request(
            now[0],
            role="validator",
            executor_id="validator-a",
        )
    )
    second = repo.acquire(
        lease_request(
            now[0],
            role="validator",
            executor_id="validator-b",
        )
    )
    assert first.scope_key != second.scope_key
    with pytest.raises(SemanticLeaseRepositoryError, match="lease_overlap"):
        repo.acquire(
            lease_request(
                now[0],
                role="validator",
                executor_id="validator-a",
                sequence_start=5,
                sequence_end=20,
            )
        )
