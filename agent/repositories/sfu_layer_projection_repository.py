"""Persistent CAS storage for Hub-materialized SFU layer projections."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_layer_projections import SfuLayerProjectionDB, SfuLayerProjectionReceiptDB


ProjectionKind = Literal["room", "publisher", "receiver"]


@dataclass(frozen=True, slots=True)
class SfuStoredLayerProjection:
    projection_id: str
    tenant_id: str
    room_id: str
    projection_kind: ProjectionKind
    subject_ref: str
    projection_ref: str
    projection_version: int
    session_projection_version: int
    membership_epoch: int
    route_epoch: int
    topology_epoch: int
    key_epoch: int
    fencing_token: int
    expected_previous_version: int
    payload: Mapping[str, object]
    payload_digest: str
    signature: str
    signature_key_id: str
    signature_algorithm: str
    signature_algorithm_version: int
    signature_key_version: int
    mode: str
    status: str
    expires_at_ms: int
    retain_until_ms: int


@dataclass(frozen=True, slots=True)
class SfuStoredProjectionReceipt:
    receipt_id: str
    tenant_id: str
    projection_ref: str
    actor_digest: str
    receipt_sequence: int
    payload: Mapping[str, object]
    payload_digest: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class SfuProjectionMutation:
    status: Literal["saved", "replayed", "conflict", "not_found"]
    projection: SfuStoredLayerProjection | None
    reason_code: str


class SfuLayerProjectionRepositoryPort(Protocol):
    def current(self, *, tenant_id: str, room_id: str, projection_kind: ProjectionKind,
                subject_ref: str) -> SfuStoredLayerProjection | None: ...
    def save(self, projection: SfuStoredLayerProjection) -> SfuProjectionMutation: ...
    def save_receipt(self, receipt: SfuStoredProjectionReceipt, *, history_max: int) -> bool: ...
    def purge(self, *, now_ms: int, limit: int) -> int: ...


class SfuLayerProjectionRepositoryError(RuntimeError):
    pass


class InMemorySfuLayerProjectionRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._projections: dict[tuple[str, str, str, str], SfuStoredLayerProjection] = {}
        self._receipts: dict[tuple[str, str, str, int], SfuStoredProjectionReceipt] = {}

    def current(self, *, tenant_id: str, room_id: str, projection_kind: ProjectionKind,
                subject_ref: str) -> SfuStoredLayerProjection | None:
        with self._lock:
            return self._projections.get((tenant_id, room_id, projection_kind, subject_ref))

    def save(self, projection: SfuStoredLayerProjection) -> SfuProjectionMutation:
        key = (projection.tenant_id, projection.room_id, projection.projection_kind, projection.subject_ref)
        with self._lock:
            current = self._projections.get(key)
            if current is not None and current.projection_version == projection.projection_version:
                if current.payload_digest == projection.payload_digest:
                    return SfuProjectionMutation("replayed", current, "sfu_projection_replayed")
                return SfuProjectionMutation("conflict", current, "sfu_projection_version_conflict")
            current_version = current.projection_version if current else 0
            if current_version != projection.expected_previous_version:
                return SfuProjectionMutation("conflict", current, "sfu_projection_cas_conflict")
            if current is not None and projection.fencing_token <= current.fencing_token:
                return SfuProjectionMutation("conflict", current, "sfu_projection_fencing_stale")
            self._projections[key] = projection
            return SfuProjectionMutation("saved", projection, "sfu_projection_saved")

    def save_receipt(self, receipt: SfuStoredProjectionReceipt, *, history_max: int) -> bool:
        key = (receipt.tenant_id, receipt.projection_ref, receipt.actor_digest, receipt.receipt_sequence)
        with self._lock:
            current = self._receipts.get(key)
            if current is not None:
                return current.payload_digest == receipt.payload_digest
            rows = sorted(
                (item for item in self._receipts.values()
                 if item.tenant_id == receipt.tenant_id and item.projection_ref == receipt.projection_ref
                 and item.actor_digest == receipt.actor_digest),
                key=lambda item: item.receipt_sequence,
            )
            for stale in rows[:max(0, len(rows) - history_max + 1)]:
                self._receipts.pop((stale.tenant_id, stale.projection_ref, stale.actor_digest, stale.receipt_sequence), None)
            self._receipts[key] = receipt
            return True

    def purge(self, *, now_ms: int, limit: int) -> int:
        with self._lock:
            projection_keys = [key for key, value in self._projections.items() if value.retain_until_ms <= now_ms][:limit]
            for key in projection_keys:
                self._projections.pop(key, None)
            for key, value in tuple(self._receipts.items()):
                if value.expires_at_ms <= now_ms:
                    self._receipts.pop(key, None)
            return len(projection_keys)


class SqlSfuLayerProjectionRepository:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def current(self, *, tenant_id: str, room_id: str, projection_kind: ProjectionKind,
                subject_ref: str) -> SfuStoredLayerProjection | None:
        try:
            with Session(self._engine) as db:
                row = _find(db, tenant_id, room_id, projection_kind, subject_ref)
                return _projection(row) if row else None
        except SQLAlchemyError as exc:
            raise SfuLayerProjectionRepositoryError("sfu_projection_store_unavailable") from exc

    def save(self, projection: SfuStoredLayerProjection) -> SfuProjectionMutation:
        try:
            with Session(self._engine) as db:
                current = _find(db, projection.tenant_id, projection.room_id,
                                projection.projection_kind, projection.subject_ref)
                if current is not None and current.projection_version == projection.projection_version:
                    stored = _projection(current)
                    if current.payload_digest == projection.payload_digest:
                        return SfuProjectionMutation("replayed", stored, "sfu_projection_replayed")
                    return SfuProjectionMutation("conflict", stored, "sfu_projection_version_conflict")
                current_version = current.projection_version if current else 0
                if current_version != projection.expected_previous_version:
                    return SfuProjectionMutation("conflict", _projection(current) if current else None, "sfu_projection_cas_conflict")
                if current is None:
                    db.add(_row(projection))
                else:
                    if projection.fencing_token <= current.fencing_token:
                        return SfuProjectionMutation("conflict", _projection(current), "sfu_projection_fencing_stale")
                    result = db.exec(sa.update(SfuLayerProjectionDB).where(
                        SfuLayerProjectionDB.id == current.id,
                        SfuLayerProjectionDB.projection_version == projection.expected_previous_version,
                        SfuLayerProjectionDB.fencing_token == current.fencing_token,
                    ).values(**_values(projection)))
                    if int(result.rowcount or 0) != 1:
                        db.rollback()
                        return SfuProjectionMutation("conflict", _projection(current), "sfu_projection_cas_conflict")
                db.commit()
                return SfuProjectionMutation("saved", projection, "sfu_projection_saved")
        except IntegrityError:
            return SfuProjectionMutation("conflict", None, "sfu_projection_cas_conflict")
        except SQLAlchemyError as exc:
            raise SfuLayerProjectionRepositoryError("sfu_projection_store_unavailable") from exc

    def save_receipt(self, receipt: SfuStoredProjectionReceipt, *, history_max: int) -> bool:
        try:
            with Session(self._engine) as db:
                existing = db.exec(select(SfuLayerProjectionReceiptDB).where(
                    SfuLayerProjectionReceiptDB.tenant_id == receipt.tenant_id,
                    SfuLayerProjectionReceiptDB.projection_ref == receipt.projection_ref,
                    SfuLayerProjectionReceiptDB.actor_digest == receipt.actor_digest,
                    SfuLayerProjectionReceiptDB.receipt_sequence == receipt.receipt_sequence,
                )).first()
                if existing is not None:
                    return existing.receipt_digest == receipt.payload_digest
                rows = db.exec(select(SfuLayerProjectionReceiptDB).where(
                    SfuLayerProjectionReceiptDB.tenant_id == receipt.tenant_id,
                    SfuLayerProjectionReceiptDB.projection_ref == receipt.projection_ref,
                    SfuLayerProjectionReceiptDB.actor_digest == receipt.actor_digest,
                ).order_by(SfuLayerProjectionReceiptDB.receipt_sequence)).all()
                stale = rows[:max(0, len(rows) - history_max + 1)]
                if stale:
                    db.exec(sa.delete(SfuLayerProjectionReceiptDB).where(
                        SfuLayerProjectionReceiptDB.id.in_([item.id for item in stale])
                    ))
                db.add(SfuLayerProjectionReceiptDB(
                    id=receipt.receipt_id, tenant_id=receipt.tenant_id,
                    projection_ref=receipt.projection_ref, actor_digest=receipt.actor_digest,
                    receipt_sequence=receipt.receipt_sequence,
                    receipt_json=dict(receipt.payload), receipt_digest=receipt.payload_digest,
                    expires_at_ms=receipt.expires_at_ms,
                ))
                db.commit()
                return True
        except IntegrityError:
            return False
        except SQLAlchemyError as exc:
            raise SfuLayerProjectionRepositoryError("sfu_projection_store_unavailable") from exc

    def purge(self, *, now_ms: int, limit: int) -> int:
        try:
            with Session(self._engine) as db:
                ids = [row.id for row in db.exec(select(SfuLayerProjectionDB).where(
                    SfuLayerProjectionDB.retain_until_ms <= now_ms,
                ).order_by(SfuLayerProjectionDB.retain_until_ms).limit(limit)).all()]
                if ids:
                    db.exec(sa.delete(SfuLayerProjectionDB).where(SfuLayerProjectionDB.id.in_(ids)))
                db.exec(sa.delete(SfuLayerProjectionReceiptDB).where(
                    SfuLayerProjectionReceiptDB.expires_at_ms <= now_ms
                ))
                db.commit()
                return len(ids)
        except SQLAlchemyError as exc:
            raise SfuLayerProjectionRepositoryError("sfu_projection_store_unavailable") from exc


def _find(db: Session, tenant: str, room: str, kind: str, subject: str) -> SfuLayerProjectionDB | None:
    return db.exec(select(SfuLayerProjectionDB).where(
        SfuLayerProjectionDB.tenant_id == tenant, SfuLayerProjectionDB.room_id == room,
        SfuLayerProjectionDB.projection_kind == kind, SfuLayerProjectionDB.subject_ref == subject,
    )).first()


def _projection(row: SfuLayerProjectionDB) -> SfuStoredLayerProjection:
    return SfuStoredLayerProjection(
        row.id, row.tenant_id, row.room_id, row.projection_kind, row.subject_ref,
        row.projection_ref, row.projection_version, row.session_projection_version,
        row.membership_epoch, row.route_epoch, row.topology_epoch, row.key_epoch,
        row.fencing_token, row.expected_previous_version,
        json.loads(json.dumps(row.payload_json)), row.payload_digest, row.signature,
        row.signature_key_id, row.signature_algorithm, row.signature_algorithm_version,
        row.signature_key_version, row.mode, row.status, row.expires_at_ms,
        row.retain_until_ms,
    )


def _row(value: SfuStoredLayerProjection) -> SfuLayerProjectionDB:
    return SfuLayerProjectionDB(id=value.projection_id, **_values(value))


def _values(value: SfuStoredLayerProjection) -> dict:
    return {
        "tenant_id": value.tenant_id, "room_id": value.room_id,
        "projection_kind": value.projection_kind, "subject_ref": value.subject_ref,
        "projection_ref": value.projection_ref, "projection_version": value.projection_version,
        "session_projection_version": value.session_projection_version,
        "membership_epoch": value.membership_epoch, "route_epoch": value.route_epoch,
        "topology_epoch": value.topology_epoch, "key_epoch": value.key_epoch,
        "fencing_token": value.fencing_token,
        "expected_previous_version": value.expected_previous_version,
        "payload_json": dict(value.payload), "payload_digest": value.payload_digest,
        "signature": value.signature, "signature_key_id": value.signature_key_id,
        "signature_algorithm": value.signature_algorithm,
        "signature_algorithm_version": value.signature_algorithm_version,
        "signature_key_version": value.signature_key_version,
        "mode": value.mode, "status": value.status,
        "expires_at_ms": value.expires_at_ms, "retain_until_ms": value.retain_until_ms,
        "updated_at": __import__("time").time(),
    }


__all__ = [
    "InMemorySfuLayerProjectionRepository", "ProjectionKind",
    "SfuLayerProjectionRepositoryError", "SfuLayerProjectionRepositoryPort",
    "SfuProjectionMutation", "SfuStoredLayerProjection", "SfuStoredProjectionReceipt",
    "SqlSfuLayerProjectionRepository",
]
