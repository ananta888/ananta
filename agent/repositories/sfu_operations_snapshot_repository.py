"""SQL repository for bounded SFU broadcast operations snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_hub_control import (
    SfuOperationsSnapshotDB,
    SfuOperationsSnapshotRecordDB,
)
from agent.models.sfu_broadcast_operations import (
    SfuBroadcastOperationsError,
    SfuBroadcastOperationsRecord,
    SfuBroadcastOperationsSnapshot,
    SfuBroadcastOperationsSourceScope,
)
from agent.repositories.sfu_hub_control_validation import (
    _finite_time,
    _safe_ref,
)


_LAYERS = ("none", "low", "medium", "high")


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


__all__ = [
    "SqlSfuBroadcastOperationsSnapshotRepository",
]
