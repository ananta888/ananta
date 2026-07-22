"""Atomic SQL ledger for Hub-owned SFU capacity reservations."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import (
    SfuCapacityLedgerDB,
    SfuCapacityReservationDB,
    SfuCapacityReservationMutationDB,
)


RESOURCE_FIELDS = (
    "cpu_millicores",
    "memory_bytes",
    "fd_count",
    "ingress_bps",
    "egress_bps",
    "receivers",
    "tracks",
    "turn_bps",
)
_UNBOUNDED = 2**62


class SfuCapacityReservationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuResourceVector:
    cpu_millicores: int = 0
    memory_bytes: int = 0
    fd_count: int = 0
    ingress_bps: int = 0
    egress_bps: int = 0
    receivers: int = 0
    tracks: int = 0
    turn_bps: int = 0

    def __post_init__(self) -> None:
        if any(
            isinstance(getattr(self, name), bool)
            or not isinstance(getattr(self, name), int)
            or getattr(self, name) < 0
            for name in RESOURCE_FIELDS
        ):
            raise ValueError("sfu_capacity_resource_invalid")

    @property
    def empty(self) -> bool:
        return all(getattr(self, name) == 0 for name in RESOURCE_FIELDS)

    def signed_delta(self, previous: "SfuResourceVector") -> dict[str, int]:
        return {
            name: getattr(self, name) - getattr(previous, name)
            for name in RESOURCE_FIELDS
        }

    def payload(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in RESOURCE_FIELDS}


@dataclass(frozen=True, slots=True)
class SfuCapacityReservationRecord:
    id: str
    tenant_id: str
    room_id: str
    cluster_id: str
    region: str
    runtime_control_mode: str
    placement_owner: str
    observed_node_id: str | None
    runtime_instance_id: str | None
    infrastructure_profile_id: str
    slo_profile_id: str
    resources: SfuResourceVector
    lease_expires_at: float
    directory_version: int
    fencing_token: int
    version: int
    status: str
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, object]:
        value = asdict(self)
        value["resources"] = self.resources.payload()
        return value


@dataclass(frozen=True, slots=True)
class SfuCapacityMutation:
    command_id: str
    operation: str
    tenant_id: str
    room_id: str
    cluster_id: str
    region: str
    runtime_control_mode: str
    placement_owner: str
    observed_node_id: str | None
    runtime_instance_id: str | None
    infrastructure_profile_id: str
    slo_profile_id: str
    resources: SfuResourceVector
    lease_ttl_seconds: float
    directory_version: int
    expected_version: int
    observation_fresh: bool
    target_admission_ready: bool


@dataclass(frozen=True, slots=True)
class SfuCapacityMutationResult:
    record: SfuCapacityReservationRecord
    replayed: bool


class SfuCapacityReservationRepositoryPort(Protocol):
    def mutate(
        self,
        mutation: SfuCapacityMutation,
        *,
        cluster_limit: SfuResourceVector,
        tenant_limit: SfuResourceVector,
        now: float | None = None,
    ) -> SfuCapacityMutationResult: ...

    def get(self, *, tenant_id: str, room_id: str) -> SfuCapacityReservationRecord | None: ...

    def release_for_room(
        self,
        *,
        tenant_id: str,
        room_id: str,
        expected_version: int,
        reason_code: str,
        now: float | None = None,
    ) -> bool: ...

    def reconcile_expired(self, *, limit: int = 100, now: float | None = None) -> int: ...

    def release_for_observed_node(
        self, *, observed_node_id: str, limit: int = 100, now: float | None = None
    ) -> int: ...


class SqlSfuCapacityReservationRepository:
    """Uses conditional ledger updates so concurrent Hubs cannot overbook."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        clock=time.time,
        mutation_retention_seconds: int = 86_400,
        purge_batch: int = 128,
    ) -> None:
        if not 60 <= mutation_retention_seconds <= 604_800 or not 1 <= purge_batch <= 1_000:
            raise ValueError("sfu_capacity_repository_limits_invalid")
        self._engine = db_engine
        self._clock = clock
        self._mutation_retention = mutation_retention_seconds
        self._purge_batch = purge_batch

    def mutate(
        self,
        mutation: SfuCapacityMutation,
        *,
        cluster_limit: SfuResourceVector,
        tenant_limit: SfuResourceVector,
        now: float | None = None,
    ) -> SfuCapacityMutationResult:
        effective_now = float(self._clock() if now is None else now)
        request_digest = _mutation_digest(mutation)
        command_digest = _digest(mutation.command_id)
        try:
            with Session(self._engine) as db:
                self._purge_expired_mutations(db, effective_now)
                replay = db.exec(
                    select(SfuCapacityReservationMutationDB).where(
                        SfuCapacityReservationMutationDB.tenant_id == mutation.tenant_id,
                        SfuCapacityReservationMutationDB.command_id_digest == command_digest,
                        SfuCapacityReservationMutationDB.expires_at.is_not(None),
                        SfuCapacityReservationMutationDB.expires_at > effective_now,
                    )
                ).first()
                if replay is not None:
                    if replay.request_digest != request_digest:
                        raise SfuCapacityReservationError("sfu_capacity_command_conflict")
                    return SfuCapacityMutationResult(
                        _record_from_payload(replay.result_json), replayed=True
                    )

                self._expire_scope(
                    db,
                    cluster_id=mutation.cluster_id,
                    region=mutation.region,
                    now=effective_now,
                )
                current = db.exec(
                    select(SfuCapacityReservationDB).where(
                        SfuCapacityReservationDB.tenant_id == mutation.tenant_id,
                        SfuCapacityReservationDB.room_id == mutation.room_id,
                    )
                ).first()
                desired, previous = self._resolve_resources(mutation, current)
                delta = desired.signed_delta(previous)
                increases = any(value > 0 for value in delta.values())
                if increases and not mutation.observation_fresh:
                    raise SfuCapacityReservationError("sfu_capacity_observation_stale")
                if increases and not mutation.target_admission_ready:
                    raise SfuCapacityReservationError("sfu_capacity_target_not_admission_ready")
                if current is not None and mutation.directory_version < current.directory_version:
                    raise SfuCapacityReservationError("sfu_capacity_directory_version_stale")

                self._ensure_ledger(db, mutation.cluster_id, mutation.region, "")
                self._ensure_ledger(
                    db, mutation.cluster_id, mutation.region, mutation.tenant_id
                )
                self._apply_delta(
                    db,
                    mutation.cluster_id,
                    mutation.region,
                    "",
                    delta,
                    cluster_limit,
                    "sfu_capacity_cluster_hard_limit",
                    effective_now,
                )
                self._apply_delta(
                    db,
                    mutation.cluster_id,
                    mutation.region,
                    mutation.tenant_id,
                    delta,
                    tenant_limit,
                    "sfu_capacity_tenant_quota",
                    effective_now,
                )
                record = self._persist_mutation(
                    db, mutation, current, desired, effective_now
                )
                db.add(
                    SfuCapacityReservationMutationDB(
                        tenant_id=mutation.tenant_id,
                        room_id=mutation.room_id,
                        reservation_id=record.id,
                        operation=mutation.operation,
                        command_id_digest=command_digest,
                        request_digest=request_digest,
                        result_json=record.payload(),
                        created_at=effective_now,
                        expires_at=effective_now + self._mutation_retention,
                    )
                )
                db.commit()
                return SfuCapacityMutationResult(record, replayed=False)
        except SfuCapacityReservationError:
            raise
        except IntegrityError as exc:
            raise SfuCapacityReservationError("sfu_capacity_concurrent_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuCapacityReservationError("sfu_capacity_store_unavailable") from exc

    def purge_expired_mutations(
        self, *, now: float | None = None, limit: int | None = None
    ) -> int:
        effective_now = float(self._clock() if now is None else now)
        bounded = self._purge_batch if limit is None else limit
        if not 1 <= bounded <= 1_000:
            raise SfuCapacityReservationError("sfu_capacity_purge_limit_invalid")
        try:
            with Session(self._engine) as db:
                count = self._purge_expired_mutations(db, effective_now, bounded)
                db.commit()
                return count
        except SQLAlchemyError as exc:
            raise SfuCapacityReservationError("sfu_capacity_store_unavailable") from exc

    def _purge_expired_mutations(
        self, db: Session, now: float, limit: int | None = None
    ) -> int:
        ids = list(
            db.exec(
                select(SfuCapacityReservationMutationDB.id)
                .where(
                    SfuCapacityReservationMutationDB.expires_at.is_not(None),
                    SfuCapacityReservationMutationDB.expires_at <= now,
                )
                .order_by(SfuCapacityReservationMutationDB.expires_at)
                .limit(self._purge_batch if limit is None else limit)
            ).all()
        )
        if ids:
            db.exec(
                sa.delete(SfuCapacityReservationMutationDB).where(
                    SfuCapacityReservationMutationDB.id.in_(ids)
                )
            )
        return len(ids)

    def get(
        self, *, tenant_id: str, room_id: str
    ) -> SfuCapacityReservationRecord | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(
                    select(SfuCapacityReservationDB).where(
                        SfuCapacityReservationDB.tenant_id == tenant_id,
                        SfuCapacityReservationDB.room_id == room_id,
                    )
                ).first()
                return None if row is None else _record_from_row(row)
        except SQLAlchemyError as exc:
            raise SfuCapacityReservationError("sfu_capacity_store_unavailable") from exc

    def release_for_room(
        self,
        *,
        tenant_id: str,
        room_id: str,
        expected_version: int,
        reason_code: str,
        now: float | None = None,
    ) -> bool:
        if not tenant_id or not room_id or not reason_code:
            raise SfuCapacityReservationError("sfu_capacity_release_scope_invalid")
        effective_now = float(self._clock() if now is None else now)
        try:
            with Session(self._engine) as db:
                row = db.exec(
                    select(SfuCapacityReservationDB)
                    .where(
                        SfuCapacityReservationDB.tenant_id == tenant_id,
                        SfuCapacityReservationDB.room_id == room_id,
                    )
                    .with_for_update()
                ).first()
                if row is None or row.status != "active":
                    return False
                if row.version != expected_version:
                    raise SfuCapacityReservationError("sfu_capacity_reconcile_conflict")
                self._release_row(db, row, "released", effective_now)
                db.commit()
                return True
        except SfuCapacityReservationError:
            raise
        except SQLAlchemyError as exc:
            raise SfuCapacityReservationError("sfu_capacity_store_unavailable") from exc

    def reconcile_expired(self, *, limit: int = 100, now: float | None = None) -> int:
        if not 1 <= limit <= 1000:
            raise SfuCapacityReservationError("sfu_capacity_reconcile_limit_invalid")
        effective_now = float(self._clock() if now is None else now)
        try:
            with Session(self._engine) as db:
                rows = tuple(
                    db.exec(
                        select(SfuCapacityReservationDB)
                        .where(
                            SfuCapacityReservationDB.status == "active",
                            SfuCapacityReservationDB.lease_expires_at <= effective_now,
                        )
                        .order_by(SfuCapacityReservationDB.lease_expires_at.asc())
                        .limit(limit)
                        .with_for_update()
                    ).all()
                )
                for row in rows:
                    self._release_row(db, row, "expired", effective_now)
                db.commit()
                return len(rows)
        except SfuCapacityReservationError:
            raise
        except SQLAlchemyError as exc:
            raise SfuCapacityReservationError("sfu_capacity_store_unavailable") from exc

    def release_for_observed_node(
        self, *, observed_node_id: str, limit: int = 100, now: float | None = None
    ) -> int:
        if not observed_node_id:
            raise SfuCapacityReservationError("sfu_capacity_node_id_required")
        effective_now = float(self._clock() if now is None else now)
        try:
            with Session(self._engine) as db:
                rows = tuple(
                    db.exec(
                        select(SfuCapacityReservationDB)
                        .where(
                            SfuCapacityReservationDB.observed_node_id == observed_node_id,
                            SfuCapacityReservationDB.status == "active",
                        )
                        .order_by(SfuCapacityReservationDB.id.asc())
                        .limit(limit)
                        .with_for_update()
                    ).all()
                )
                for row in rows:
                    self._release_row(db, row, "released", effective_now)
                db.commit()
                return len(rows)
        except SfuCapacityReservationError:
            raise
        except SQLAlchemyError as exc:
            raise SfuCapacityReservationError("sfu_capacity_store_unavailable") from exc

    @staticmethod
    def _resolve_resources(
        mutation: SfuCapacityMutation,
        current: SfuCapacityReservationDB | None,
    ) -> tuple[SfuResourceVector, SfuResourceVector]:
        previous = SfuResourceVector() if current is None else _resources_from_row(current)
        if mutation.operation == "create":
            if current is not None or mutation.expected_version != 0:
                raise SfuCapacityReservationError("sfu_capacity_create_conflict")
            if mutation.resources.empty:
                raise SfuCapacityReservationError("sfu_capacity_empty_reservation")
            return mutation.resources, previous
        if current is None or current.status != "active":
            raise SfuCapacityReservationError("sfu_capacity_active_reservation_not_found")
        if current.version != mutation.expected_version:
            raise SfuCapacityReservationError("sfu_capacity_version_conflict")
        if mutation.operation == "renew":
            if mutation.resources != previous:
                raise SfuCapacityReservationError("sfu_capacity_renew_resource_change")
            return previous, previous
        if mutation.operation == "resize":
            if mutation.resources.empty:
                raise SfuCapacityReservationError("sfu_capacity_empty_reservation")
            return mutation.resources, previous
        if mutation.operation == "release":
            if not mutation.resources.empty:
                raise SfuCapacityReservationError("sfu_capacity_release_resources_nonzero")
            return SfuResourceVector(), previous
        raise SfuCapacityReservationError("sfu_capacity_operation_invalid")

    @staticmethod
    def _persist_mutation(
        db: Session,
        mutation: SfuCapacityMutation,
        current: SfuCapacityReservationDB | None,
        desired: SfuResourceVector,
        now: float,
    ) -> SfuCapacityReservationRecord:
        status = "released" if mutation.operation == "release" else "active"
        if current is None:
            row = SfuCapacityReservationDB(
                id=f"sfu-capacity-{uuid.uuid4().hex}",
                tenant_id=mutation.tenant_id,
                room_id=mutation.room_id,
                cluster_id=mutation.cluster_id,
                region=mutation.region,
                runtime_control_mode=mutation.runtime_control_mode,
                placement_owner=mutation.placement_owner,
                observed_node_id=mutation.observed_node_id,
                runtime_instance_id=mutation.runtime_instance_id,
                infrastructure_profile_id=mutation.infrastructure_profile_id,
                slo_profile_id=mutation.slo_profile_id,
                lease_expires_at=now + mutation.lease_ttl_seconds,
                directory_version=mutation.directory_version,
                fencing_token=1,
                version=1,
                status=status,
                created_at=now,
                updated_at=now,
                **desired.payload(),
            )
            db.add(row)
            db.flush()
            return _record_from_row(row)
        values = {
            **desired.payload(),
            "observed_node_id": (
                mutation.observed_node_id
                if mutation.observed_node_id is not None
                else current.observed_node_id
            ),
            "lease_expires_at": (
                now if status == "released" else now + mutation.lease_ttl_seconds
            ),
            "directory_version": mutation.directory_version,
            "fencing_token": current.fencing_token + 1,
            "version": current.version + 1,
            "status": status,
            "updated_at": now,
        }
        result = db.exec(
            sa.update(SfuCapacityReservationDB)
            .where(
                SfuCapacityReservationDB.id == current.id,
                SfuCapacityReservationDB.version == current.version,
                SfuCapacityReservationDB.fencing_token == current.fencing_token,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise SfuCapacityReservationError("sfu_capacity_version_conflict")
        db.flush()
        db.expire_all()
        updated = db.get(SfuCapacityReservationDB, current.id)
        if updated is None:
            raise SfuCapacityReservationError("sfu_capacity_active_reservation_not_found")
        return _record_from_row(updated)

    def _expire_scope(
        self, db: Session, *, cluster_id: str, region: str, now: float
    ) -> None:
        rows = tuple(
            db.exec(
                select(SfuCapacityReservationDB)
                .where(
                    SfuCapacityReservationDB.cluster_id == cluster_id,
                    SfuCapacityReservationDB.region == region,
                    SfuCapacityReservationDB.status == "active",
                    SfuCapacityReservationDB.lease_expires_at <= now,
                )
                .with_for_update()
            ).all()
        )
        for row in rows:
            self._release_row(db, row, "expired", now)

    def _release_row(
        self, db: Session, row: SfuCapacityReservationDB, status: str, now: float
    ) -> None:
        current = _resources_from_row(row)
        delta = {name: -getattr(current, name) for name in RESOURCE_FIELDS}
        unlimited = SfuResourceVector(**{name: _UNBOUNDED for name in RESOURCE_FIELDS})
        self._ensure_ledger(db, row.cluster_id, row.region, "")
        self._ensure_ledger(db, row.cluster_id, row.region, row.tenant_id)
        self._apply_delta(
            db, row.cluster_id, row.region, "", delta, unlimited,
            "sfu_capacity_ledger_drift", now
        )
        self._apply_delta(
            db, row.cluster_id, row.region, row.tenant_id, delta, unlimited,
            "sfu_capacity_ledger_drift", now
        )
        values = {
            **SfuResourceVector().payload(),
            "status": status,
            "lease_expires_at": now,
            "fencing_token": row.fencing_token + 1,
            "version": row.version + 1,
            "updated_at": now,
        }
        result = db.exec(
            sa.update(SfuCapacityReservationDB)
            .where(
                SfuCapacityReservationDB.id == row.id,
                SfuCapacityReservationDB.version == row.version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise SfuCapacityReservationError("sfu_capacity_reconcile_conflict")

    @staticmethod
    def _ensure_ledger(
        db: Session, cluster_id: str, region: str, tenant_scope: str
    ) -> None:
        values = {
            "id": _ledger_id(cluster_id, region, tenant_scope),
            "cluster_id": cluster_id,
            "region": region,
            "tenant_scope": tenant_scope,
            "version": 1,
            **SfuResourceVector().payload(),
        }
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            db.exec(
                sqlite_insert(SfuCapacityLedgerDB)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["cluster_id", "region", "tenant_scope"]
                )
            )
        elif dialect == "postgresql":
            db.exec(
                postgresql_insert(SfuCapacityLedgerDB)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["cluster_id", "region", "tenant_scope"]
                )
            )
        else:
            existing = db.get(SfuCapacityLedgerDB, values["id"])
            if existing is None:
                db.add(SfuCapacityLedgerDB(**values))
                db.flush()

    @staticmethod
    def _apply_delta(
        db: Session,
        cluster_id: str,
        region: str,
        tenant_scope: str,
        delta: dict[str, int],
        limit: SfuResourceVector,
        reason_code: str,
        now: float,
    ) -> None:
        predicates = [
            SfuCapacityLedgerDB.cluster_id == cluster_id,
            SfuCapacityLedgerDB.region == region,
            SfuCapacityLedgerDB.tenant_scope == tenant_scope,
        ]
        values: dict[str, object] = {
            "version": SfuCapacityLedgerDB.version + 1,
            "updated_at": now,
        }
        for name in RESOURCE_FIELDS:
            column = getattr(SfuCapacityLedgerDB, name)
            next_value = column + delta[name]
            predicates.extend((next_value >= 0, next_value <= getattr(limit, name)))
            values[name] = next_value
        result = db.exec(
            sa.update(SfuCapacityLedgerDB).where(*predicates).values(**values)
        )
        if result.rowcount != 1:
            raise SfuCapacityReservationError(reason_code)


def _resources_from_row(row: SfuCapacityReservationDB) -> SfuResourceVector:
    return SfuResourceVector(**{name: int(getattr(row, name)) for name in RESOURCE_FIELDS})


def _record_from_row(row: SfuCapacityReservationDB) -> SfuCapacityReservationRecord:
    return SfuCapacityReservationRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        room_id=row.room_id,
        cluster_id=row.cluster_id,
        region=row.region,
        runtime_control_mode=row.runtime_control_mode,
        placement_owner=row.placement_owner,
        observed_node_id=row.observed_node_id,
        runtime_instance_id=row.runtime_instance_id,
        infrastructure_profile_id=row.infrastructure_profile_id,
        slo_profile_id=row.slo_profile_id,
        resources=_resources_from_row(row),
        lease_expires_at=row.lease_expires_at,
        directory_version=row.directory_version,
        fencing_token=row.fencing_token,
        version=row.version,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _record_from_payload(value: dict) -> SfuCapacityReservationRecord:
    try:
        expected = set(SfuCapacityReservationRecord.__dataclass_fields__)
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError
        payload = dict(value)
        resources = payload["resources"]
        if not isinstance(resources, Mapping) or set(resources) != set(RESOURCE_FIELDS):
            raise ValueError
        payload["resources"] = SfuResourceVector(**resources)
        for name in ("lease_expires_at", "created_at", "updated_at"):
            number = payload[name]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)):
                raise ValueError
        for name in ("directory_version", "fencing_token", "version"):
            number = payload[name]
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise ValueError
        if payload["status"] not in {"active", "released", "expired"}:
            raise ValueError
        for name in (
            "id",
            "tenant_id",
            "room_id",
            "cluster_id",
            "region",
            "runtime_control_mode",
            "placement_owner",
            "infrastructure_profile_id",
            "slo_profile_id",
        ):
            if not isinstance(payload[name], str) or not payload[name]:
                raise ValueError
        for name in ("observed_node_id", "runtime_instance_id"):
            if payload[name] is not None and not isinstance(payload[name], str):
                raise ValueError
        return SfuCapacityReservationRecord(**payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise SfuCapacityReservationError("sfu_capacity_receipt_invalid") from exc


def _mutation_digest(value: SfuCapacityMutation) -> str:
    payload = asdict(value)
    payload["resources"] = value.resources.payload()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ledger_id(cluster_id: str, region: str, tenant_scope: str) -> str:
    return "sfu-capacity-ledger-" + hashlib.sha256(
        f"{cluster_id}\0{region}\0{tenant_scope}".encode("utf-8")
    ).hexdigest()


__all__ = [
    "RESOURCE_FIELDS",
    "SfuCapacityMutation",
    "SfuCapacityMutationResult",
    "SfuCapacityReservationError",
    "SfuCapacityReservationRecord",
    "SfuCapacityReservationRepositoryPort",
    "SfuResourceVector",
    "SqlSfuCapacityReservationRepository",
]
