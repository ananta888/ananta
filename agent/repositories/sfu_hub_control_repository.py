"""SQL repositories for durable, tenant-bounded SFU Hub control state."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from dataclasses import asdict
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_hub_control import (
    SfuCommandIdempotencyLedgerDB,
    SfuFanoutReconciliationControlDB,
    SfuFanoutReconciliationOutcomeDB,
    SfuOperationsSnapshotDB,
    SfuOperationsSnapshotRecordDB,
    SfuScopeEpochAuthorityDB,
    SfuScopeEpochGrantDB,
)
from agent.services.sfu_broadcast_command_service import (
    SfuBroadcastCommandError,
    SfuBroadcastCommandResult,
)
from agent.services.sfu_broadcast_operations_read_model import (
    SfuBroadcastOperationsError,
    SfuBroadcastOperationsRecord,
    SfuBroadcastOperationsSnapshot,
    SfuBroadcastOperationsSourceScope,
)
from agent.services.sfu_browser_capability_ingestion_service import (
    SfuCapabilityAdmissionScope,
)
from agent.services.sfu_fanout_reconciliation_service import (
    ReconciliationPhase,
    RouteReconciliationCursor,
    RouteReconciliationItemOutcome,
    RouteReconciliationLease,
    RouteReconciliationScope,
)
from agent.services.sfu_layer_projection_service import SfuProjectionScope


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_LAYERS = ("none", "low", "medium", "high")


class SfuHubControlRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlSfuBroadcastOperationsSnapshotRepository:
    """Normalized operations snapshots with bounded history and no JSON payload."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        clock: Callable[[], float] = time.time,
        max_records: int = 2_000,
        purge_batch: int = 128,
    ) -> None:
        if not 1 <= max_records <= 10_000 or not 1 <= purge_batch <= 1_000:
            raise ValueError("sfu_operations_repository_limits_invalid")
        self._engine = db_engine
        self._clock = clock
        self._max_records = max_records
        self._purge_batch = purge_batch

    def save(
        self,
        snapshot: SfuBroadcastOperationsSnapshot,
        *,
        retention_seconds: int = 3_600,
        max_snapshots: int = 8,
    ) -> str:
        now = _finite_time(self._clock(), "sfu_operations_clock_invalid")
        if (
            not _safe_ref(snapshot.version)
            or not 60 <= retention_seconds <= 86_400
            or not 1 <= max_snapshots <= 64
            or len(snapshot.records) > self._max_records
        ):
            raise SfuBroadcastOperationsError(
                "sfu_operations_snapshot_invalid", 503
            )
        documents = [
            _operations_record_document(record) for record in snapshot.records
        ]
        digest = hashlib.sha256(
            json.dumps(
                {"version": snapshot.version, "records": documents},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        snapshot_id = "sfu-ops-" + hashlib.sha256(
            snapshot.version.encode("utf-8")
        ).hexdigest()[:32]
        try:
            with Session(self._engine) as db:
                current = db.exec(
                    select(SfuOperationsSnapshotDB).where(
                        SfuOperationsSnapshotDB.snapshot_version
                        == snapshot.version
                    )
                ).first()
                if current is not None:
                    if (
                        current.snapshot_digest == digest
                        and current.record_count == len(documents)
                    ):
                        return "replayed"
                    raise SfuBroadcastOperationsError(
                        "sfu_operations_snapshot_version_conflict", 409
                    )
                db.add(
                    SfuOperationsSnapshotDB(
                        id=snapshot_id,
                        snapshot_version=snapshot.version,
                        snapshot_digest=digest,
                        record_count=len(documents),
                        retain_until=now + retention_seconds,
                        created_at=now,
                    )
                )
                for ordinal, document in enumerate(documents):
                    db.add(
                        SfuOperationsSnapshotRecordDB(
                            id=f"{snapshot_id}-{ordinal}",
                            snapshot_id=snapshot_id,
                            ordinal=ordinal,
                            **document,
                        )
                    )
                self._purge_snapshots(
                    db, now=now, max_snapshots=max_snapshots
                )
                db.commit()
                return "saved"
        except SfuBroadcastOperationsError:
            raise
        except IntegrityError as exc:
            raise SfuBroadcastOperationsError(
                "sfu_operations_snapshot_version_conflict", 409
            ) from exc
        except SQLAlchemyError as exc:
            raise SfuBroadcastOperationsError(
                "sfu_operations_store_unavailable", 503
            ) from exc

    def load(
        self,
        *,
        snapshot_version: str | None,
        max_records: int,
        scope: SfuBroadcastOperationsSourceScope | None = None,
    ) -> SfuBroadcastOperationsSnapshot:
        now = _finite_time(self._clock(), "sfu_operations_clock_invalid")
        if (
            isinstance(max_records, bool)
            or not 1 <= max_records <= self._max_records
            or (
                snapshot_version is not None
                and not _safe_ref(snapshot_version)
            )
        ):
            raise SfuBroadcastOperationsError(
                "sfu_operations_snapshot_query_invalid", 503
            )
        try:
            with Session(self._engine) as db:
                statement = select(SfuOperationsSnapshotDB).where(
                    SfuOperationsSnapshotDB.retain_until > now
                )
                if snapshot_version is None:
                    statement = statement.order_by(
                        SfuOperationsSnapshotDB.created_at.desc()
                    )
                else:
                    statement = statement.where(
                        SfuOperationsSnapshotDB.snapshot_version
                        == snapshot_version
                    )
                header = db.exec(statement).first()
                if header is None:
                    if snapshot_version is not None:
                        raise SfuBroadcastOperationsError(
                            "sfu_operations_cursor_stale", 409
                        )
                    raise SfuBroadcastOperationsError(
                        "sfu_operations_snapshot_missing", 503
                    )
                records = select(SfuOperationsSnapshotRecordDB).where(
                    SfuOperationsSnapshotRecordDB.snapshot_id == header.id
                )
                if scope is not None:
                    if scope.tenant_refs is not None:
                        records = records.where(
                            SfuOperationsSnapshotRecordDB.tenant_ref.in_(scope.tenant_refs)
                        )
                    if scope.region_refs is not None:
                        records = records.where(
                            SfuOperationsSnapshotRecordDB.region.in_(scope.region_refs)
                        )
                    for column, value in (
                        (SfuOperationsSnapshotRecordDB.owner_subject, scope.owner_subject),
                        (SfuOperationsSnapshotRecordDB.room_ref, scope.room_ref),
                        (SfuOperationsSnapshotRecordDB.receiver_ref, scope.receiver_ref),
                    ):
                        if value is not None:
                            records = records.where(column == value)
                rows = db.exec(
                    records.order_by(SfuOperationsSnapshotRecordDB.ordinal).limit(max_records)
                ).all()
                if scope is None and len(rows) != min(header.record_count, max_records):
                    raise SfuBroadcastOperationsError(
                        "sfu_operations_snapshot_incomplete", 503
                    )
                return SfuBroadcastOperationsSnapshot(
                    header.snapshot_version,
                    tuple(_operations_record(row) for row in rows),
                )
        except SfuBroadcastOperationsError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastOperationsError(
                "sfu_operations_store_unavailable", 503
            ) from exc

    def purge(self, *, now: float | None = None, limit: int = 128) -> int:
        timestamp = _finite_time(
            self._clock() if now is None else now,
            "sfu_operations_clock_invalid",
        )
        if not 1 <= limit <= 1_000:
            raise SfuBroadcastOperationsError(
                "sfu_operations_purge_limit_invalid", 503
            )
        try:
            with Session(self._engine) as db:
                ids = [
                    row.id
                    for row in db.exec(
                        select(SfuOperationsSnapshotDB)
                        .where(
                            SfuOperationsSnapshotDB.retain_until <= timestamp
                        )
                        .order_by(SfuOperationsSnapshotDB.retain_until)
                        .limit(limit)
                    ).all()
                ]
                _delete_snapshots(db, ids)
                db.commit()
                return len(ids)
        except SQLAlchemyError as exc:
            raise SfuBroadcastOperationsError(
                "sfu_operations_store_unavailable", 503
            ) from exc

    def _purge_snapshots(
        self, db: Session, *, now: float, max_snapshots: int
    ) -> None:
        expired = [
            row.id
            for row in db.exec(
                select(SfuOperationsSnapshotDB)
                .where(SfuOperationsSnapshotDB.retain_until <= now)
                .order_by(SfuOperationsSnapshotDB.retain_until)
                .limit(self._purge_batch)
            ).all()
        ]
        overflow = [
            row.id
            for row in db.exec(
                select(SfuOperationsSnapshotDB)
                .order_by(SfuOperationsSnapshotDB.created_at.desc())
                .offset(max_snapshots)
                .limit(self._purge_batch)
            ).all()
        ]
        _delete_snapshots(db, sorted(set(expired + overflow)))


class SqlSfuBroadcastCommandLedger:
    """Restart-stable command idempotency without request payload storage."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        max_entries: int = 4_096,
        retention_seconds: int = 3_600,
        purge_batch: int = 128,
        delivery_retry_seconds: int = 5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            not 1 <= max_entries <= 1_000_000
            or not 60 <= retention_seconds <= 86_400
            or not 1 <= purge_batch <= 1_000
            or not 1 <= delivery_retry_seconds < retention_seconds
        ):
            raise ValueError("sfu_command_ledger_limits_invalid")
        self._engine = db_engine
        self._max_entries = max_entries
        self._retention = retention_seconds
        self._purge_batch = purge_batch
        self._delivery_retry = delivery_retry_seconds
        self._clock = clock

    def claim(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        now: float,
    ) -> tuple[str, SfuBroadcastCommandResult | None]:
        _validate_ledger_digests(scope_digest, key_digest, request_digest)
        timestamp = _finite_time(now, "sfu_command_ledger_clock_invalid")
        try:
            with Session(self._engine) as db:
                _purge_ledger(db, timestamp, self._purge_batch)
                current = _find_ledger(db, scope_digest, key_digest)
                if current is not None and current.expires_at > timestamp:
                    result = self._claim_existing(db, current, request_digest, timestamp)
                    db.commit()
                    return result
                if current is not None:
                    db.delete(current)
                    db.flush()
                capacity = len(
                    db.exec(
                        select(SfuCommandIdempotencyLedgerDB.id)
                        .order_by(
                            SfuCommandIdempotencyLedgerDB.created_at
                        )
                        .limit(self._max_entries)
                    ).all()
                )
                if capacity >= self._max_entries:
                    db.rollback()
                    return "capacity", None
                db.add(
                    SfuCommandIdempotencyLedgerDB(
                        id=_ledger_id(scope_digest, key_digest),
                        scope_digest=scope_digest,
                        key_digest=key_digest,
                        request_digest=request_digest,
                        status="pending",
                        operation_id=_ledger_operation_id(scope_digest, key_digest, request_digest),
                        delivery_state="delivering",
                        delivery_attempts=1,
                        version=1,
                        expires_at=timestamp + self._retention,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                db.commit()
                return "claimed", None
        except IntegrityError:
            with Session(self._engine) as db:
                current = _find_ledger(db, scope_digest, key_digest)
                if current is None:
                    return "capacity", None
                result = self._claim_existing(db, current, request_digest, timestamp)
                db.commit()
                return result
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc

    def complete(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        result: SfuBroadcastCommandResult,
    ) -> None:
        _validate_ledger_digests(scope_digest, key_digest, request_digest)
        try:
            with Session(self._engine) as db:
                current = _find_ledger(db, scope_digest, key_digest)
                if (
                    current is None
                    or current.request_digest != request_digest
                ):
                    raise SfuBroadcastCommandError(
                        "sfu_command_idempotency_state_invalid", 503
                    )
                if current.status == "completed":
                    if _ledger_result(current) == result:
                        return
                    raise SfuBroadcastCommandError(
                        "sfu_command_idempotency_state_invalid", 503
                    )
                updated = db.exec(
                    sa.update(SfuCommandIdempotencyLedgerDB)
                    .where(
                        SfuCommandIdempotencyLedgerDB.id == current.id,
                        SfuCommandIdempotencyLedgerDB.version
                        == current.version,
                        SfuCommandIdempotencyLedgerDB.status == "pending",
                    )
                    .values(
                        status="completed",
                        delivery_state="completed",
                        result_accepted=result.accepted,
                        result_effective_version=result.effective_version,
                        result_state=result.state,
                        result_reason_code=result.reason_code,
                        result_code=result.reason_code,
                        result_version=result.effective_version,
                        result_command_ref=result.command_ref,
                        version=current.version + 1,
                        updated_at=_finite_time(
                            self._clock(), "sfu_command_ledger_clock_invalid"
                        ),
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    raise SfuBroadcastCommandError(
                        "sfu_command_idempotency_state_invalid", 503
                    )
                db.commit()
        except SfuBroadcastCommandError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc

    def abort(
        self, scope_digest: str, key_digest: str, request_digest: str
    ) -> None:
        _validate_ledger_digests(scope_digest, key_digest, request_digest)
        try:
            with Session(self._engine) as db:
                db.exec(
                    sa.delete(SfuCommandIdempotencyLedgerDB).where(
                        SfuCommandIdempotencyLedgerDB.scope_digest
                        == scope_digest,
                        SfuCommandIdempotencyLedgerDB.key_digest == key_digest,
                        SfuCommandIdempotencyLedgerDB.request_digest
                        == request_digest,
                        SfuCommandIdempotencyLedgerDB.status == "pending",
                    )
                )
                db.commit()
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc

    def _claim_existing(
        self,
        db: Session,
        current: SfuCommandIdempotencyLedgerDB,
        request_digest: str,
        timestamp: float,
    ) -> tuple[str, SfuBroadcastCommandResult | None]:
        if current.request_digest != request_digest:
            return "conflict", None
        if current.status == "completed":
            result = _ledger_result(current)
            return ("replay", result) if result is not None else ("conflict", None)
        if (
            current.delivery_state == "delivering"
            and current.updated_at + self._delivery_retry > timestamp
        ):
            return "in_progress", None
        updated = db.exec(
            sa.update(SfuCommandIdempotencyLedgerDB)
            .where(
                SfuCommandIdempotencyLedgerDB.id == current.id,
                SfuCommandIdempotencyLedgerDB.version == current.version,
                SfuCommandIdempotencyLedgerDB.status == "pending",
            )
            .values(
                operation_id=current.operation_id
                or _ledger_operation_id(
                    current.scope_digest,
                    current.key_digest,
                    current.request_digest,
                ),
                delivery_state="delivering",
                delivery_attempts=current.delivery_attempts + 1,
                version=current.version + 1,
                updated_at=timestamp,
            )
        )
        if int(updated.rowcount or 0) != 1:
            return "in_progress", None
        return "claimed", None

    def purge(self, *, now: float, limit: int | None = None) -> int:
        timestamp = _finite_time(now, "sfu_command_ledger_clock_invalid")
        bounded = self._purge_batch if limit is None else limit
        if not 1 <= bounded <= 1_000:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_purge_limit_invalid", 503
            )
        try:
            with Session(self._engine) as db:
                count = _purge_ledger(db, timestamp, bounded)
                db.commit()
                return count
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc


class SqlSfuFanoutReconciliationControlRepository:
    """Lease, checkpoint and content-free outcome store for one tenant room."""

    def __init__(
        self,
        *,
        owner_digest_secret: bytes,
        db_engine=default_engine,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        control_retention_ms: int = 86_400_000,
        outcome_retention_ms: int = 3_600_000,
        outcomes_max_per_scope: int = 10_000,
        purge_batch: int = 128,
    ) -> None:
        if (
            len(owner_digest_secret) < 32
            or not 60_000 <= control_retention_ms <= 604_800_000
            or not 60_000 <= outcome_retention_ms <= 86_400_000
            or not 1 <= outcomes_max_per_scope <= 1_000_000
            or not 1 <= purge_batch <= 1_000
        ):
            raise ValueError("sfu_reconciliation_repository_limits_invalid")
        self._secret = bytes(owner_digest_secret)
        self._engine = db_engine
        self._clock_ms = clock_ms
        self._control_retention = control_retention_ms
        self._outcome_retention = outcome_retention_ms
        self._outcome_capacity = outcomes_max_per_scope
        self._purge_batch = purge_batch

    def acquire(
        self,
        *,
        scope: RouteReconciliationScope,
        owner_ref: str,
        now_ms: int,
        lease_ttl_ms: int,
    ) -> RouteReconciliationLease | None:
        _validate_reconciliation_scope(scope, owner_ref)
        if (
            type(now_ms) is not int
            or now_ms <= 0
            or type(lease_ttl_ms) is not int
            or not 100 <= lease_ttl_ms <= 300_000
        ):
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_lease_input_invalid"
            )
        owner_digest = self._owner_digest(owner_ref)
        try:
            with Session(self._engine) as db:
                current = _find_reconciliation_control(db, scope)
                if current is not None and current.lease_expires_at_ms > now_ms:
                    return None
                expires = now_ms + lease_ttl_ms
                if current is None:
                    token = 1
                    db.add(
                        SfuFanoutReconciliationControlDB(
                            id=_reconciliation_control_id(scope),
                            tenant_id=scope.tenant_ref,
                            room_id=scope.room_ref,
                            owner_digest=owner_digest,
                            fencing_token=token,
                            lease_expires_at_ms=expires,
                            version=1,
                            retain_until_ms=now_ms + self._control_retention,
                            created_at=now_ms / 1000.0,
                            updated_at=now_ms / 1000.0,
                        )
                    )
                else:
                    token = current.fencing_token + 1
                    updated = db.exec(
                        sa.update(SfuFanoutReconciliationControlDB)
                        .where(
                            SfuFanoutReconciliationControlDB.id == current.id,
                            SfuFanoutReconciliationControlDB.version
                            == current.version,
                            SfuFanoutReconciliationControlDB.lease_expires_at_ms
                            <= now_ms,
                        )
                        .values(
                            owner_digest=owner_digest,
                            fencing_token=token,
                            lease_expires_at_ms=expires,
                            version=current.version + 1,
                            retain_until_ms=now_ms
                            + self._control_retention,
                            updated_at=now_ms / 1000.0,
                        )
                    )
                    if int(updated.rowcount or 0) != 1:
                        db.rollback()
                        return None
                db.commit()
                return RouteReconciliationLease(
                    scope, owner_ref, str(token), expires
                )
        except IntegrityError:
            return None
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def release(self, lease: RouteReconciliationLease) -> None:
        now_ms = _positive_clock_ms(self._clock_ms())
        token = _lease_token(lease)
        try:
            with Session(self._engine) as db:
                updated = db.exec(
                    sa.update(SfuFanoutReconciliationControlDB)
                    .where(
                        SfuFanoutReconciliationControlDB.tenant_id
                        == lease.scope.tenant_ref,
                        SfuFanoutReconciliationControlDB.room_id
                        == lease.scope.room_ref,
                        SfuFanoutReconciliationControlDB.owner_digest
                        == self._owner_digest(lease.owner_ref),
                        SfuFanoutReconciliationControlDB.fencing_token
                        == token,
                    )
                    .values(
                        owner_digest="",
                        lease_expires_at_ms=now_ms,
                        version=SfuFanoutReconciliationControlDB.version + 1,
                        retain_until_ms=now_ms + self._control_retention,
                        updated_at=now_ms / 1000.0,
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_lease_stale"
                    )
                db.commit()
        except SfuHubControlRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def save(
        self,
        *,
        lease: RouteReconciliationLease,
        cursor: RouteReconciliationCursor | None,
    ) -> None:
        now_ms = _positive_clock_ms(self._clock_ms())
        token = _lease_token(lease)
        if cursor is not None and (
            not isinstance(cursor.phase, ReconciliationPhase)
            or (
                cursor.token is not None
                and (
                    not isinstance(cursor.token, str)
                    or len(cursor.token.encode("utf-8")) > 512
                )
            )
        ):
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_checkpoint_invalid"
            )
        try:
            with Session(self._engine) as db:
                updated = db.exec(
                    sa.update(SfuFanoutReconciliationControlDB)
                    .where(
                        SfuFanoutReconciliationControlDB.tenant_id
                        == lease.scope.tenant_ref,
                        SfuFanoutReconciliationControlDB.room_id
                        == lease.scope.room_ref,
                        SfuFanoutReconciliationControlDB.owner_digest
                        == self._owner_digest(lease.owner_ref),
                        SfuFanoutReconciliationControlDB.fencing_token
                        == token,
                        SfuFanoutReconciliationControlDB.lease_expires_at_ms
                        > now_ms,
                    )
                    .values(
                        checkpoint_phase=(
                            cursor.phase.value if cursor is not None else None
                        ),
                        checkpoint_token=(
                            cursor.token if cursor is not None else None
                        ),
                        version=SfuFanoutReconciliationControlDB.version + 1,
                        retain_until_ms=now_ms + self._control_retention,
                        updated_at=now_ms / 1000.0,
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_lease_stale"
                    )
                db.commit()
        except SfuHubControlRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def load_checkpoint(
        self, *, scope: RouteReconciliationScope
    ) -> RouteReconciliationCursor | None:
        _validate_reconciliation_scope(scope, "checkpoint-reader")
        try:
            with Session(self._engine) as db:
                row = _find_reconciliation_control(db, scope)
                if row is None or row.checkpoint_phase is None:
                    return None
                try:
                    phase = ReconciliationPhase(row.checkpoint_phase)
                except ValueError as exc:
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_checkpoint_corrupt"
                    ) from exc
                return RouteReconciliationCursor(
                    phase, row.checkpoint_token
                )
        except SfuHubControlRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def record(
        self,
        *,
        lease: RouteReconciliationLease,
        outcome: RouteReconciliationItemOutcome,
    ) -> None:
        now_ms = _positive_clock_ms(self._clock_ms())
        token = _lease_token(lease)
        document = {
            "action": outcome.action.value,
            "reason_code": outcome.reason_code,
            "retryable": outcome.retryable,
            "mutation_outcome": (
                outcome.mutation.outcome.value
                if outcome.mutation is not None
                else None
            ),
            "mutation_reason_code": (
                outcome.mutation.reason_code.value
                if outcome.mutation is not None
                else None
            ),
        }
        if (
            not _safe_ref(outcome.candidate_ref)
            or not _safe_reason(outcome.reason_code)
            or not isinstance(outcome.retryable, bool)
        ):
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_outcome_invalid"
            )
        candidate_digest = _plain_digest(outcome.candidate_ref)
        outcome_digest = _plain_digest(
            json.dumps(
                document, sort_keys=True, separators=(",", ":")
            )
        )
        row_id = "sfu-rec-out-" + _plain_digest(
            "\0".join(
                (
                    lease.scope.tenant_ref,
                    lease.scope.room_ref,
                    candidate_digest,
                    str(token),
                )
            )
        )[:32]
        try:
            with Session(self._engine) as db:
                control = _find_reconciliation_control(db, lease.scope)
                if (
                    control is None
                    or control.owner_digest
                    != self._owner_digest(lease.owner_ref)
                    or control.fencing_token != token
                    or control.lease_expires_at_ms <= now_ms
                ):
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_lease_stale"
                    )
                existing = db.get(SfuFanoutReconciliationOutcomeDB, row_id)
                if existing is not None:
                    if existing.outcome_digest == outcome_digest:
                        return
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_outcome_conflict"
                    )
                _purge_reconciliation_outcomes(
                    db,
                    tenant_id=lease.scope.tenant_ref,
                    room_id=lease.scope.room_ref,
                    now_ms=now_ms,
                    limit=self._purge_batch,
                )
                active_count = len(
                    db.exec(
                        select(SfuFanoutReconciliationOutcomeDB.id)
                        .where(
                            SfuFanoutReconciliationOutcomeDB.tenant_id
                            == lease.scope.tenant_ref,
                            SfuFanoutReconciliationOutcomeDB.room_id
                            == lease.scope.room_ref,
                        )
                        .limit(self._outcome_capacity)
                    ).all()
                )
                if active_count >= self._outcome_capacity:
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_outcome_capacity"
                    )
                db.add(
                    SfuFanoutReconciliationOutcomeDB(
                        id=row_id,
                        tenant_id=lease.scope.tenant_ref,
                        room_id=lease.scope.room_ref,
                        candidate_digest=candidate_digest,
                        outcome_digest=outcome_digest,
                        action=document["action"],
                        reason_code=outcome.reason_code,
                        retryable=outcome.retryable,
                        mutation_outcome=document["mutation_outcome"],
                        mutation_reason_code=document[
                            "mutation_reason_code"
                        ],
                        fencing_token=token,
                        retain_until_ms=now_ms
                        + self._outcome_retention,
                        created_at=now_ms / 1000.0,
                    )
                )
                db.commit()
        except SfuHubControlRepositoryError:
            raise
        except IntegrityError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_outcome_conflict"
            ) from exc
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def _owner_digest(self, owner_ref: str) -> str:
        return hmac.new(
            self._secret,
            b"ananta:sfu-reconciliation-owner:v1\0"
            + owner_ref.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


class SqlSfuScopeEpochResolver:
    """Read-only authority adapter; a missing row always denies the scope."""

    def __init__(
        self,
        *,
        identity_digest_secret: bytes,
        db_engine=default_engine,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if len(identity_digest_secret) < 32:
            raise ValueError("sfu_scope_epoch_secret_invalid")
        self._secret = bytes(identity_digest_secret)
        self._engine = db_engine
        self._clock_ms = clock_ms

    def resolve(
        self, *, tenant_id: str, room_id: str, actor_id: str
    ) -> SfuCapabilityAdmissionScope | None:
        if not all(_safe_ref(value) for value in (tenant_id, room_id, actor_id)):
            return None
        now_ms = _positive_clock_ms(self._clock_ms())
        try:
            with Session(self._engine) as db:
                row = self._active_scope(
                    db, tenant_id, room_id, actor_id, now_ms
                )
                if row is None:
                    return None
                return SfuCapabilityAdmissionScope(
                    tenant_id,
                    room_id,
                    actor_id,
                    row.admission_epoch,
                    row.membership_epoch,
                )
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_scope_epoch_store_unavailable"
            ) from exc

    def authorize(
        self,
        *,
        tenant_id: str,
        room_id: str,
        actor_id: str,
        projection_kind: str,
        subject_ref: str,
    ) -> SfuProjectionScope | None:
        if (
            projection_kind not in {"room", "publisher", "receiver"}
            or not all(
                _safe_ref(value)
                for value in (
                    tenant_id,
                    room_id,
                    actor_id,
                    subject_ref,
                )
            )
        ):
            return None
        now_ms = _positive_clock_ms(self._clock_ms())
        try:
            with Session(self._engine) as db:
                scope = self._active_scope(
                    db, tenant_id, room_id, actor_id, now_ms
                )
                if scope is None:
                    return None
                if projection_kind == "room":
                    if subject_ref != room_id:
                        return None
                else:
                    if min(
                        scope.route_epoch,
                        scope.topology_epoch,
                        scope.key_epoch,
                    ) <= 0:
                        return None
                    grant = db.exec(
                        select(SfuScopeEpochGrantDB).where(
                            SfuScopeEpochGrantDB.tenant_id == tenant_id,
                            SfuScopeEpochGrantDB.room_id == room_id,
                            SfuScopeEpochGrantDB.actor_digest
                            == sfu_scope_identity_digest(
                                self._secret, "actor", actor_id
                            ),
                            SfuScopeEpochGrantDB.projection_kind
                            == projection_kind,
                            SfuScopeEpochGrantDB.subject_digest
                            == sfu_scope_identity_digest(
                                self._secret, "subject", subject_ref
                            ),
                            SfuScopeEpochGrantDB.status == "active",
                            SfuScopeEpochGrantDB.expires_at_ms > now_ms,
                            SfuScopeEpochGrantDB.scope_version
                            == scope.version,
                            SfuScopeEpochGrantDB.membership_epoch
                            == scope.membership_epoch,
                            SfuScopeEpochGrantDB.fencing_token
                            == scope.fencing_token,
                        )
                    ).first()
                    if grant is None:
                        return None
                return SfuProjectionScope(
                    tenant_id=tenant_id,
                    room_id=room_id,
                    actor_id=actor_id,
                    membership_epoch=scope.membership_epoch,
                    route_epoch=scope.route_epoch,
                    topology_epoch=scope.topology_epoch,
                    key_epoch=scope.key_epoch,
                )
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_scope_epoch_store_unavailable"
            ) from exc

    def _active_scope(
        self,
        db: Session,
        tenant_id: str,
        room_id: str,
        actor_id: str,
        now_ms: int,
    ) -> SfuScopeEpochAuthorityDB | None:
        return db.exec(
            select(SfuScopeEpochAuthorityDB).where(
                SfuScopeEpochAuthorityDB.tenant_id == tenant_id,
                SfuScopeEpochAuthorityDB.room_id == room_id,
                SfuScopeEpochAuthorityDB.actor_digest
                == sfu_scope_identity_digest(
                    self._secret, "actor", actor_id
                ),
                SfuScopeEpochAuthorityDB.status == "active",
                SfuScopeEpochAuthorityDB.expires_at_ms > now_ms,
            )
        ).first()


def sfu_scope_identity_digest(
    secret: bytes, domain: str, value: str
) -> str:
    if (
        len(secret) < 32
        or domain not in {"actor", "subject"}
        or not _safe_ref(value)
    ):
        raise ValueError("sfu_scope_epoch_digest_input_invalid")
    return hmac.new(
        bytes(secret),
        f"ananta:sfu-scope-{domain}:v1\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _operations_record_document(
    record: SfuBroadcastOperationsRecord,
) -> dict[str, object]:
    values = asdict(record)
    distribution = values.pop("layer_distribution")
    if (
        not isinstance(distribution, dict)
        or set(distribution) - set(_LAYERS)
        or any(
            type(value) is not int or value < 0
            for value in distribution.values()
        )
    ):
        raise SfuBroadcastOperationsError(
            "sfu_operations_snapshot_record_invalid", 503
        )
    for name in (
        "tenant_ref",
        "region",
        "room_ref",
        "owner_subject",
        "receiver_ref",
    ):
        if not _safe_ref(values[name]):
            raise SfuBroadcastOperationsError(
                "sfu_operations_snapshot_record_invalid", 503
            )
    if (
        isinstance(values["observed_at_seconds"], bool)
        or not isinstance(values["observed_at_seconds"], (int, float))
        or not math.isfinite(float(values["observed_at_seconds"]))
        or values["observed_at_seconds"] < 0
    ):
        raise SfuBroadcastOperationsError(
            "sfu_operations_snapshot_record_invalid", 503
        )
    for name in (
        "cohort_size",
        "queue_depth",
        "ingress_bytes_per_second",
        "egress_bytes_per_second",
        "turn_bytes_per_second",
    ):
        if type(values[name]) is not int or values[name] < 0:
            raise SfuBroadcastOperationsError(
                "sfu_operations_snapshot_record_invalid", 503
            )
    for layer in _LAYERS:
        values[f"layer_{layer}_count"] = distribution.get(layer, 0)
    return values


def _operations_record(
    row: SfuOperationsSnapshotRecordDB,
) -> SfuBroadcastOperationsRecord:
    return SfuBroadcastOperationsRecord(
        observed_at_seconds=row.observed_at_seconds,
        tenant_ref=row.tenant_ref,
        region=row.region,
        room_ref=row.room_ref,
        owner_subject=row.owner_subject,
        receiver_ref=row.receiver_ref,
        cohort_size=row.cohort_size,
        group_status=row.group_status,
        route_status=row.route_status,
        epoch_class=row.epoch_class,
        topology=row.topology,
        health=row.health,
        requested_layer=row.requested_layer,
        allowed_layer=row.allowed_layer,
        effective_layer=row.effective_layer,
        layer_distribution={
            "none": row.layer_none_count,
            "low": row.layer_low_count,
            "medium": row.layer_medium_count,
            "high": row.layer_high_count,
        },
        queue_depth=row.queue_depth,
        drop_reason=row.drop_reason,
        ingress_bytes_per_second=row.ingress_bytes_per_second,
        egress_bytes_per_second=row.egress_bytes_per_second,
        turn_bytes_per_second=row.turn_bytes_per_second,
        rekey_status=row.rekey_status,
        failover_status=row.failover_status,
        capacity_profile=row.capacity_profile,
        gate_state=row.gate_state,
    )


def _delete_snapshots(db: Session, ids: list[str]) -> None:
    if not ids:
        return
    db.exec(
        sa.delete(SfuOperationsSnapshotRecordDB).where(
            SfuOperationsSnapshotRecordDB.snapshot_id.in_(ids)
        )
    )
    db.exec(
        sa.delete(SfuOperationsSnapshotDB).where(
            SfuOperationsSnapshotDB.id.in_(ids)
        )
    )


def _validate_ledger_digests(*values: str) -> None:
    if any(
        not isinstance(value, str) or not _DIGEST.fullmatch(value)
        for value in values
    ):
        raise SfuBroadcastCommandError(
            "sfu_command_idempotency_digest_invalid", 503
        )


def _ledger_id(scope_digest: str, key_digest: str) -> str:
    return "sfu-cmd-" + _plain_digest(
        scope_digest + "\0" + key_digest
    )[:32]


def _find_ledger(
    db: Session, scope_digest: str, key_digest: str
) -> SfuCommandIdempotencyLedgerDB | None:
    return db.exec(
        select(SfuCommandIdempotencyLedgerDB).where(
            SfuCommandIdempotencyLedgerDB.scope_digest == scope_digest,
            SfuCommandIdempotencyLedgerDB.key_digest == key_digest,
        )
    ).first()


def _ledger_operation_id(
    scope_digest: str, key_digest: str, request_digest: str
) -> str:
    digest = hashlib.sha256(
        f"{scope_digest}\0{key_digest}\0{request_digest}".encode("ascii")
    ).hexdigest()
    return "sfcop1." + digest[:32]


def _ledger_result(
    row: SfuCommandIdempotencyLedgerDB,
) -> SfuBroadcastCommandResult | None:
    if (
        row.status != "completed"
        or row.result_accepted is None
        or (row.result_version is None and row.result_effective_version is None)
        or row.result_state is None
        or (row.result_code is None and row.result_reason_code is None)
        or row.result_command_ref is None
    ):
        return None
    return SfuBroadcastCommandResult(
        row.result_accepted,
        row.result_version
        if row.result_version is not None
        else row.result_effective_version,
        row.result_state,
        row.result_code if row.result_code is not None else row.result_reason_code,
        row.result_command_ref,
    )


def _purge_ledger(db: Session, now: float, limit: int) -> int:
    ids = db.exec(
        select(SfuCommandIdempotencyLedgerDB.id)
        .where(SfuCommandIdempotencyLedgerDB.expires_at <= now)
        .order_by(SfuCommandIdempotencyLedgerDB.expires_at)
        .limit(limit)
    ).all()
    if ids:
        db.exec(
            sa.delete(SfuCommandIdempotencyLedgerDB).where(
                SfuCommandIdempotencyLedgerDB.id.in_(ids)
            )
        )
        db.flush()
    return len(ids)


def _validate_reconciliation_scope(
    scope: RouteReconciliationScope, owner_ref: str
) -> None:
    if not all(
        _safe_ref(value)
        for value in (scope.tenant_ref, scope.room_ref, owner_ref)
    ):
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_scope_invalid"
        )


def _find_reconciliation_control(
    db: Session, scope: RouteReconciliationScope
) -> SfuFanoutReconciliationControlDB | None:
    return db.exec(
        select(SfuFanoutReconciliationControlDB).where(
            SfuFanoutReconciliationControlDB.tenant_id
            == scope.tenant_ref,
            SfuFanoutReconciliationControlDB.room_id == scope.room_ref,
        )
    ).first()


def _reconciliation_control_id(scope: RouteReconciliationScope) -> str:
    return "sfu-rec-" + _plain_digest(
        scope.tenant_ref + "\0" + scope.room_ref
    )[:32]


def _lease_token(lease: RouteReconciliationLease) -> int:
    if (
        not isinstance(lease.fencing_token, str)
        or not lease.fencing_token.isdecimal()
    ):
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_lease_invalid"
        )
    value = int(lease.fencing_token)
    if value <= 0:
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_lease_invalid"
        )
    return value


def _purge_reconciliation_outcomes(
    db: Session,
    *,
    tenant_id: str,
    room_id: str,
    now_ms: int,
    limit: int,
) -> int:
    ids = db.exec(
        select(SfuFanoutReconciliationOutcomeDB.id)
        .where(
            SfuFanoutReconciliationOutcomeDB.tenant_id == tenant_id,
            SfuFanoutReconciliationOutcomeDB.room_id == room_id,
            SfuFanoutReconciliationOutcomeDB.retain_until_ms <= now_ms,
        )
        .order_by(SfuFanoutReconciliationOutcomeDB.retain_until_ms)
        .limit(limit)
    ).all()
    if ids:
        db.exec(
            sa.delete(SfuFanoutReconciliationOutcomeDB).where(
                SfuFanoutReconciliationOutcomeDB.id.in_(ids)
            )
        )
        db.flush()
    return len(ids)


def _plain_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_ref(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_REF.fullmatch(value))


def _safe_reason(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and bool(re.fullmatch(r"[a-z0-9][a-z0-9_:-]*", value))
    )


def _finite_time(value: object, reason_code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(reason_code)
    return float(value)


def _positive_clock_ms(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_clock_invalid"
        )
    return value


__all__ = [
    "SfuHubControlRepositoryError",
    "SqlSfuBroadcastCommandLedger",
    "SqlSfuBroadcastOperationsSnapshotRepository",
    "SqlSfuFanoutReconciliationControlRepository",
    "SqlSfuScopeEpochResolver",
    "sfu_scope_identity_digest",
]
