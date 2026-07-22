from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from agent.db_models.turn_observer_identities import (
    TurnObserverEnrollmentRateLimitDB,
    TurnObserverIdentityMutationDB,
)
from agent.repositories.turn_observer_identity_repository import (
    SqlTurnObserverIdentityRepository,
)


def test_observer_receipts_and_rate_buckets_are_ttl_bounded(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'observer-receipts.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TurnObserverIdentityMutationDB.__table__,
            TurnObserverEnrollmentRateLimitDB.__table__,
        ],
    )
    clock = [100.0]
    repository = SqlTurnObserverIdentityRepository(
        db_engine=engine,
        clock=lambda: clock[0],
        mutation_retention_seconds=60,
    )
    mutation = TurnObserverIdentityMutationDB(
        identity_id="identity-a",
        pool_id="pool-a",
        instance_id="instance-a",
        operation="enroll",
        expected_version=0,
        result_version=1,
        result_status="active",
        result_region="eu-1",
        result_role="turn_observer",
        result_audience="turn-observation",
        result_recovery_evidence_required=False,
        actor="operator-a",
        reason_code="turn_observer_enrolled",
        idempotency_key_digest="a" * 64,
        request_digest="b" * 64,
        response_json={},
        audited_at=clock[0],
        expires_at=clock[0] + 60,
    )
    with Session(engine) as db:
        db.add(mutation)
        db.commit()
    assert repository.receipt(actor="operator-a", key_digest="a" * 64) is not None
    repository.consume_rate_limit(
        actor="operator-a",
        source_digest="c" * 64,
        now=clock[0],
        window_seconds=60,
        attempts_max=2,
    )
    clock[0] = 160.0
    assert repository.receipt(actor="operator-a", key_digest="a" * 64) is None
    assert repository.purge_expired() == 2
