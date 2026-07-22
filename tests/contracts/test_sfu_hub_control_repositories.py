from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.sfu_hub_control import (
    SfuCommandIdempotencyLedgerDB,
    SfuFanoutReconciliationControlDB,
    SfuFanoutReconciliationOutcomeDB,
    SfuOperationsSnapshotDB,
    SfuOperationsSnapshotRecordDB,
    SfuScopeEpochAuthorityDB,
    SfuScopeEpochGrantDB,
)
from agent.repositories.sfu_hub_control_repository import (
    SqlSfuBroadcastCommandLedger,
    SqlSfuBroadcastOperationsSnapshotRepository,
    SqlSfuFanoutReconciliationControlRepository,
    SqlSfuScopeEpochResolver,
    sfu_scope_identity_digest,
)
from agent.services.sfu_broadcast_command_service import (
    SfuBroadcastCommandResult,
)
from agent.services.sfu_broadcast_operations_read_model import (
    SfuBroadcastOperationsRecord,
    SfuBroadcastOperationsSnapshot,
    SfuBroadcastOperationsSnapshotPort,
)
from agent.services.sfu_browser_capability_ingestion_service import (
    SfuCapabilityAdmissionScopePort,
)
from agent.services.sfu_fanout_reconciliation_service import (
    ReconciliationAction,
    ReconciliationPhase,
    RouteReconciliationCheckpointPort,
    RouteReconciliationCursor,
    RouteReconciliationItemOutcome,
    RouteReconciliationLeasePort,
    RouteReconciliationOutcomePort,
    RouteReconciliationScope,
)
from agent.services.sfu_layer_projection_service import (
    SfuProjectionScopeAuthorizerPort,
)


TABLES = (
    SfuOperationsSnapshotDB.__table__,
    SfuOperationsSnapshotRecordDB.__table__,
    SfuCommandIdempotencyLedgerDB.__table__,
    SfuFanoutReconciliationControlDB.__table__,
    SfuFanoutReconciliationOutcomeDB.__table__,
    SfuScopeEpochAuthorityDB.__table__,
    SfuScopeEpochGrantDB.__table__,
)


def _engine(path: Path):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )


def _create(path: Path):
    db_engine = _engine(path)
    SQLModel.metadata.create_all(db_engine, tables=TABLES)
    return db_engine


def _record(observed_at: float = 100.0) -> SfuBroadcastOperationsRecord:
    return SfuBroadcastOperationsRecord(
        observed_at,
        "tenant-a",
        "eu-central",
        "room-a",
        "owner-a",
        "receiver-a",
        12,
        "active",
        "applied",
        "current",
        "sfu",
        "healthy",
        "low",
        "low",
        "low",
        {"none": 0, "low": 12, "medium": 0, "high": 0},
        0,
        "none",
        100,
        200,
        0,
        "converged",
        "none",
        "legacy_8",
        "observe_only",
    )


def test_sql_operations_snapshot_survives_repository_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operations.db"
    first_engine = _create(path)
    first = SqlSfuBroadcastOperationsSnapshotRepository(
        db_engine=first_engine, clock=lambda: 100.0
    )
    snapshot = SfuBroadcastOperationsSnapshot("snapshot-v1", (_record(),))

    assert first.save(snapshot) == "saved"
    assert first.save(snapshot) == "replayed"
    first_engine.dispose()

    second_engine = _engine(path)
    second: SfuBroadcastOperationsSnapshotPort = (
        SqlSfuBroadcastOperationsSnapshotRepository(
            db_engine=second_engine, clock=lambda: 101.0
        )
    )
    restored = second.load(snapshot_version="snapshot-v1", max_records=10)

    assert restored == snapshot


def test_sql_command_ledger_replays_completed_result_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "commands.db"
    first_engine = _create(path)
    first = SqlSfuBroadcastCommandLedger(db_engine=first_engine)
    scope = hashlib.sha256(b"scope").hexdigest()
    key = hashlib.sha256(b"key").hexdigest()
    request = hashlib.sha256(b"request").hexdigest()
    result = SfuBroadcastCommandResult(
        True,
        4,
        "active",
        "sfu_broadcast_started",
        "sfc1.command",
    )

    assert first.claim(scope, key, request, 100.0) == ("claimed", None)
    first.complete(scope, key, request, result)
    first_engine.dispose()

    second = SqlSfuBroadcastCommandLedger(db_engine=_engine(path))
    status, replay = second.claim(scope, key, request, 101.0)

    assert status == "replay"
    assert replay == result
    assert second.claim(
        scope, key, hashlib.sha256(b"different").hexdigest(), 101.0
    ) == ("conflict", None)


def test_sql_command_ledger_recovers_stale_delivery_with_same_operation_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "command-recovery.db"
    db_engine = _create(path)
    ledger = SqlSfuBroadcastCommandLedger(
        db_engine=db_engine,
        delivery_retry_seconds=5,
        clock=lambda: 106.0,
    )
    scope = hashlib.sha256(b"scope").hexdigest()
    key = hashlib.sha256(b"key").hexdigest()
    request = hashlib.sha256(b"request").hexdigest()
    assert ledger.claim(scope, key, request, 100.0) == ("claimed", None)
    assert ledger.claim(scope, key, request, 104.0) == ("in_progress", None)
    with Session(db_engine) as db:
        first_operation = db.exec(select(SfuCommandIdempotencyLedgerDB)).one().operation_id
    assert ledger.claim(scope, key, request, 105.0) == ("claimed", None)
    with Session(db_engine) as db:
        row = db.exec(select(SfuCommandIdempotencyLedgerDB)).one()
        assert row.operation_id == first_operation
        assert row.delivery_attempts == 2


def test_reconciliation_lease_checkpoint_and_outcome_are_fenced_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reconciliation.db"
    first_engine = _create(path)
    now = [1_000]
    first = SqlSfuFanoutReconciliationControlRepository(
        db_engine=first_engine,
        owner_digest_secret=b"o" * 32,
        clock_ms=lambda: now[0],
    )
    leases: RouteReconciliationLeasePort = first
    checkpoints: RouteReconciliationCheckpointPort = first
    outcomes: RouteReconciliationOutcomePort = first
    scope = RouteReconciliationScope("tenant-a", "room-a")
    lease = leases.acquire(
        scope=scope,
        owner_ref="hub-a",
        now_ms=now[0],
        lease_ttl_ms=5_000,
    )
    assert lease is not None
    cursor = RouteReconciliationCursor(ReconciliationPhase.ENSURE, "cursor-1")
    checkpoints.save(lease=lease, cursor=cursor)
    outcomes.record(
        lease=lease,
        outcome=RouteReconciliationItemOutcome(
            "candidate-a",
            object(),
            ReconciliationAction.CONVERGED,
            "reconcile_projection_current",
            False,
        ),
    )
    leases.release(lease)
    first_engine.dispose()

    second = SqlSfuFanoutReconciliationControlRepository(
        db_engine=_engine(path),
        owner_digest_secret=b"o" * 32,
        clock_ms=lambda: now[0],
    )
    assert second.load_checkpoint(scope=scope) == cursor
    next_lease = second.acquire(
        scope=scope,
        owner_ref="hub-b",
        now_ms=now[0],
        lease_ttl_ms=5_000,
    )
    assert next_lease is not None
    assert int(next_lease.fencing_token) > int(lease.fencing_token)


def test_scope_epoch_resolver_requires_authority_and_subject_grant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scopes.db"
    db_engine = _create(path)
    secret = b"s" * 32
    actor_digest = sfu_scope_identity_digest(secret, "actor", "actor-a")
    subject_digest = sfu_scope_identity_digest(
        secret, "subject", "subscription-a"
    )
    with Session(db_engine) as db:
        db.add(
            SfuScopeEpochAuthorityDB(
                id="scope-a",
                tenant_id="tenant-a",
                room_id="room-a",
                actor_digest=actor_digest,
                admission_epoch=3,
                membership_epoch=4,
                route_epoch=5,
                topology_epoch=6,
                key_epoch=7,
                fencing_token=8,
                version=9,
                status="active",
                expires_at_ms=2_000,
                retain_until_ms=3_000,
            )
        )
        db.add(
            SfuScopeEpochGrantDB(
                id="grant-a",
                tenant_id="tenant-a",
                room_id="room-a",
                actor_digest=actor_digest,
                projection_kind="receiver",
                subject_digest=subject_digest,
                scope_version=9,
                membership_epoch=4,
                fencing_token=8,
                status="active",
                expires_at_ms=2_000,
                retain_until_ms=3_000,
            )
        )
        db.commit()

    resolver = SqlSfuScopeEpochResolver(
        db_engine=db_engine,
        identity_digest_secret=secret,
        clock_ms=lambda: 1_000,
    )
    capability_port: SfuCapabilityAdmissionScopePort = resolver
    projection_port: SfuProjectionScopeAuthorizerPort = resolver

    capability = capability_port.resolve(
        tenant_id="tenant-a", room_id="room-a", actor_id="actor-a"
    )
    projection = projection_port.authorize(
        tenant_id="tenant-a",
        room_id="room-a",
        actor_id="actor-a",
        projection_kind="receiver",
        subject_ref="subscription-a",
    )

    assert capability is not None
    assert (capability.admission_epoch, capability.membership_epoch) == (3, 4)
    assert projection is not None
    assert (
        projection.membership_epoch,
        projection.route_epoch,
        projection.topology_epoch,
        projection.key_epoch,
    ) == (4, 5, 6, 7)
    assert (
        projection_port.authorize(
            tenant_id="tenant-a",
            room_id="room-a",
            actor_id="actor-a",
            projection_kind="receiver",
            subject_ref="subscription-b",
        )
        is None
    )


def test_new_control_tables_store_no_payload_json_or_secret_columns() -> None:
    for table in TABLES:
        names = {column.name for column in table.columns}
        assert not any(
            marker in name
            for name in names
            for marker in ("payload", "secret", "raw_document", "options_json")
        )
