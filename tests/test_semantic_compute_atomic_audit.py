from __future__ import annotations

import concurrent.futures
import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import (
    SemanticComputeLeaseDB,
    SemanticComputeScheduleReceiptDB,
    SemanticMediaAuditOutboxDB,
    SemanticSessionMembershipDB,
)
from agent.repositories.semantic_contract_repository import (
    ContractMutation,
    SemanticContractRepository,
    SemanticPrincipal,
)
from agent.repositories.semantic_lease_repository import (
    LeaseRequest,
    SemanticLeaseRepository,
    SemanticLeaseRepositoryError,
)
from agent.services.semantic_contract_service import SemanticContractService
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from ananta_contracts.semantic_compute import canonical_json
from tests.semantic_compute_support import compute_contract


def _stack(*, now: list[float] | None = None):
    clock = now or [1_000.0]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    recorder = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: int(clock[0] * 1_000),
        ),
        secret=b"semantic-compute-atomic-audit-test-key" * 2,
    )
    leases = SemanticLeaseRepository(
        db_engine=engine,
        clock=lambda: clock[0],
        clock_skew_seconds=0,
        audit=recorder,
    )
    contracts = SemanticContractRepository(db_engine=engine, clock=lambda: clock[0])
    return clock, engine, recorder, leases, contracts


def _lease_request(now: float, **values) -> LeaseRequest:
    defaults = {
        "tenant_id": "tenant-a",
        "owner_subject": "owner-a",
        "contract_id": "contract-a",
        "contract_digest": "a" * 64,
        "session_id": "session-a",
        "epoch": 1,
        "task_type": "visual_extract",
        "audience": "viewer-a",
        "role": "primary",
        "executor_id": "worker-a",
        "sequence_start": 0,
        "sequence_end": 9,
        "resource_budget": {
            "cpu_ms": 100,
            "memory_bytes": 1_048_576,
            "artifact_bytes": 1_024,
        },
        "ttl_seconds": 30.0,
        "deadline_at": now + 60.0,
    }
    defaults.update(values)
    return LeaseRequest(**defaults)


def _rows(engine, model):
    with Session(engine) as db:
        return list(db.exec(select(model)))


def _active_contract(contracts: SemanticContractRepository, now: float):
    principal = SemanticPrincipal("tenant-a", "owner-a")
    payload = compute_contract(now_ms=int(now * 1_000))
    item, _ = contracts.create(
        principal,
        contract_id=payload["contract_id"],
        request_digest=hashlib.sha256(canonical_json(payload)).hexdigest(),
        idempotency_key="schedule-contract-create",
        payload=payload,
        status="active",
    )
    return item


def test_schedule_receipt_leases_and_audit_commands_are_exactly_once_under_concurrency() -> None:
    now, engine, _audit, leases, contracts = _stack()
    contract = _active_contract(contracts, now[0])
    request = _lease_request(
        now[0],
        contract_id=contract.id,
        contract_digest=contract.digest,
    )

    def schedule(_index: int):
        return leases.schedule_once(
            tenant_id="tenant-a",
            owner_subject="owner-a",
            contract_id=contract.id,
            idempotency_key="schedule-atomic-once",
            request_digest="b" * 64,
            requests=(request,),
            result_payload={
                "contract_id": contract.id,
                "contract_revision": contract.revision,
                "epoch": 1,
            },
            expires_at=now[0] + 60,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(schedule, range(40)))

    assert len({item.leases[0].id for item in results}) == 1
    assert sum(not item.replayed for item in results) == 1
    assert len(_rows(engine, SemanticComputeLeaseDB)) == 1
    assert len(_rows(engine, SemanticComputeScheduleReceiptDB)) == 1
    outbox = _rows(engine, SemanticMediaAuditOutboxDB)
    assert len(outbox) == 1
    assert outbox[0].transition == "acquired"


def test_missing_audit_outbox_rolls_back_fence_lease_and_schedule_receipt() -> None:
    now, engine, _audit, leases, contracts = _stack()
    contract = _active_contract(contracts, now[0])
    SemanticMediaAuditOutboxDB.__table__.drop(engine)

    with pytest.raises(SemanticLeaseRepositoryError, match="semantic_audit_unavailable"):
        leases.schedule_once(
            tenant_id="tenant-a",
            owner_subject="owner-a",
            contract_id=contract.id,
            idempotency_key="schedule-audit-unavailable",
            request_digest="c" * 64,
            requests=(
                _lease_request(
                    now[0],
                    contract_id=contract.id,
                    contract_digest=contract.digest,
                ),
            ),
            result_payload={
                "contract_id": contract.id,
                "contract_revision": contract.revision,
                "epoch": 1,
            },
            expires_at=now[0] + 60,
        )

    SemanticMediaAuditOutboxDB.__table__.create(engine)
    assert _rows(engine, SemanticComputeLeaseDB) == []
    assert _rows(engine, SemanticComputeScheduleReceiptDB) == []


def test_lease_lifecycle_and_idempotent_replays_emit_one_command_per_transition() -> None:
    now, engine, _audit, leases, _contracts = _stack()
    lease = leases.acquire(_lease_request(now[0]))
    renewed = leases.renew(
        lease_id=lease.id,
        fencing_token=lease.fencing_token,
        expected_version=lease.version,
        ttl_seconds=20,
    )
    reduced, replayed = leases.reduce_idempotent(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        lease_id=lease.id,
        fencing_token=lease.fencing_token,
        expected_version=renewed.version,
        resource_budget={
            "cpu_ms": 50,
            "memory_bytes": 524_288,
            "artifact_bytes": 512,
        },
        expires_at=now[0] + 10,
        idempotency_key="lease-reduce-atomic",
        request_digest="d" * 64,
    )
    assert replayed is False
    replay, replayed = leases.reduce_idempotent(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        lease_id=lease.id,
        fencing_token=lease.fencing_token,
        expected_version=renewed.version,
        resource_budget={
            "cpu_ms": 50,
            "memory_bytes": 524_288,
            "artifact_bytes": 512,
        },
        expires_at=now[0] + 10,
        idempotency_key="lease-reduce-atomic",
        request_digest="d" * 64,
    )
    assert replayed is True and replay.version == reduced.version
    revoked, replayed = leases.revoke_scoped_idempotent(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        lease_id=lease.id,
        fencing_token=lease.fencing_token,
        expected_version=reduced.version,
        idempotency_key="lease-revoke-atomic",
        request_digest="e" * 64,
    )
    assert replayed is False and revoked.status == "revoked"
    replay, replayed = leases.revoke_scoped_idempotent(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        lease_id=lease.id,
        fencing_token=lease.fencing_token,
        expected_version=reduced.version,
        idempotency_key="lease-revoke-atomic",
        request_digest="e" * 64,
    )
    assert replayed is True and replay.status == "revoked"

    rows = _rows(engine, SemanticMediaAuditOutboxDB)
    assert [item.transition for item in rows] == [
        "acquired",
        "renewed",
        "reduced",
        "revoked",
    ]
    assert len({item.idempotency_digest for item in rows}) == 4


def test_expiry_reconciler_commits_expired_state_with_its_audit_command() -> None:
    now, engine, _audit, leases, _contracts = _stack()
    lease = leases.acquire(_lease_request(now[0], ttl_seconds=1))
    now[0] += 2

    assert leases.expire_due(limit=10) == 1
    assert leases.get(lease.id).status == "expired"
    rows = _rows(engine, SemanticMediaAuditOutboxDB)
    assert [item.transition for item in rows] == ["acquired", "expired"]


def test_membership_put_and_contract_revision_lease_cascade_share_atomic_outbox() -> None:
    now, engine, audit, leases, contracts = _stack()
    principal = SemanticPrincipal("tenant-a", "owner-a")
    membership_service = SemanticContractService(
        contracts,
        audit=audit,
        lease_revoker=leases,
        feature_enabled=lambda: True,
    )
    membership_service.establish_membership(
        principal,
        session_id="session-a",
        epoch=1,
        role="owner",
        permitted=True,
        expires_at=now[0] + 100,
    )
    assert len(_rows(engine, SemanticSessionMembershipDB)) == 1

    first = compute_contract(now_ms=int(now[0] * 1_000))
    created, _ = contracts.create(
        principal,
        contract_id=first["contract_id"],
        request_digest=hashlib.sha256(canonical_json(first)).hexdigest(),
        idempotency_key="contract-create-atomic",
        payload=first,
        status="active",
    )
    lease = leases.acquire(
        _lease_request(
            now[0],
            contract_id=created.id,
            contract_digest=created.digest,
        )
    )
    second = compute_contract(now_ms=int(now[0] * 1_000), revision=2)
    contract_event = audit.prepare_transition(
        idempotency_key="contract-counter-atomic",
        tenant_id="tenant-a",
        scope="semantic-contract:session-a",
        event_type="semantic_contract",
        transition="countered",
        reason_code="hub_confirmed",
        epoch=1,
        contract_ref=second["contract_digest"],
    )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_lease_audit BEFORE INSERT ON semantic_media_audit_outbox "
            "WHEN NEW.event_type = 'semantic_lease' "
            "BEGIN SELECT RAISE(FAIL, 'lease audit unavailable'); END"
        )
    with pytest.raises(Exception):
        contracts.mutate(
            principal,
            contract_id=created.id,
            mutation=ContractMutation(
                "counter",
                "contract-counter-atomic",
                hashlib.sha256(canonical_json(second)).hexdigest(),
                1,
                created.digest,
                second,
                "countered",
            ),
            audit_event=contract_event,
            lease_revoker=leases,
        )
    assert contracts.get(principal, created.id).revision == 1
    assert leases.get(lease.id).status == "active"

    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER reject_lease_audit")
    updated, replayed = contracts.mutate(
        principal,
        contract_id=created.id,
        mutation=ContractMutation(
            "counter",
            "contract-counter-atomic",
            hashlib.sha256(canonical_json(second)).hexdigest(),
            1,
            created.digest,
            second,
            "countered",
        ),
        audit_event=contract_event,
        lease_revoker=leases,
    )
    assert replayed is False and updated.revision == 2
    assert leases.get(lease.id).status == "revoked"
    transitions = [item.transition for item in _rows(engine, SemanticMediaAuditOutboxDB)]
    assert transitions.count("membership_granted") == 1
    assert transitions.count("countered") == 1
    assert transitions.count("revoked") == 1


def test_concurrent_schedule_and_contract_revision_cannot_expose_old_digest_authority() -> None:
    now, engine, audit, leases, contracts = _stack()
    principal = SemanticPrincipal("tenant-a", "owner-a")
    contract = _active_contract(contracts, now[0])
    request = _lease_request(
        now[0],
        contract_id=contract.id,
        contract_digest=contract.digest,
    )
    second = compute_contract(now_ms=int(now[0] * 1_000), revision=2)
    contract_event = audit.prepare_transition(
        idempotency_key="contract-race-counter",
        tenant_id="tenant-a",
        scope="semantic-contract:session-a",
        event_type="semantic_contract",
        transition="countered",
        reason_code="hub_confirmed",
        epoch=1,
        contract_ref=second["contract_digest"],
    )

    def schedule():
        try:
            return leases.schedule_once(
                tenant_id="tenant-a",
                owner_subject="owner-a",
                contract_id=contract.id,
                idempotency_key="schedule-contract-race",
                request_digest="f" * 64,
                requests=(request,),
                result_payload={
                    "contract_id": contract.id,
                    "contract_revision": contract.revision,
                    "epoch": 1,
                },
                expires_at=now[0] + 60,
            )
        except SemanticLeaseRepositoryError as exc:
            return exc.reason_code

    def revise():
        return contracts.mutate(
            principal,
            contract_id=contract.id,
            mutation=ContractMutation(
                "counter",
                "contract-race-counter",
                hashlib.sha256(canonical_json(second)).hexdigest(),
                1,
                contract.digest,
                second,
                "countered",
            ),
            audit_event=contract_event,
            lease_revoker=leases,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        scheduled_future = pool.submit(schedule)
        revised_future = pool.submit(revise)
        scheduled = scheduled_future.result()
        revised, replayed = revised_future.result()

    assert replayed is False and revised.revision == 2
    if isinstance(scheduled, str):
        assert scheduled == "stale_contract_authority"
    rows = _rows(engine, SemanticComputeLeaseDB)
    assert all(
        row.status != "active" or row.contract_digest == revised.digest
        for row in rows
    )
