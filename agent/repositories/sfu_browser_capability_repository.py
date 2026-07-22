"""CAS repositories for normalized, short-lived browser capability state."""

from __future__ import annotations

import json
import threading
from dataclasses import replace

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_browser_capabilities import SfuBrowserCapabilityDB
from agent.services.sfu_browser_capability_port import (
    SfuBrowserCapabilitySnapshot,
    SfuBrowserCapabilityWriteResult,
    unknown_capability,
)


class SfuBrowserCapabilityRepositoryError(RuntimeError):
    pass


class InMemorySfuBrowserCapabilityRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rows: dict[tuple[str, str, str], SfuBrowserCapabilitySnapshot] = {}

    def read(self, *, tenant_id: str, room_id: str, browser_pseudonym: str,
             admission_epoch: int, membership_epoch: int, now_ms: int) -> SfuBrowserCapabilitySnapshot:
        with self._lock:
            row = self._rows.get((tenant_id, room_id, browser_pseudonym))
            if row is None:
                return unknown_capability(
                    tenant_id=tenant_id, room_id=room_id, browser_pseudonym=browser_pseudonym,
                    admission_epoch=admission_epoch, membership_epoch=membership_epoch,
                )
            if row.admission_epoch != admission_epoch or row.membership_epoch != membership_epoch:
                return replace(row, state="stale")
            return replace(row, state="stale") if row.expires_at_ms <= now_ms else row

    def save(self, snapshot: SfuBrowserCapabilitySnapshot, *, expected_version: int,
             room_cardinality_max: int, now_ms: int) -> SfuBrowserCapabilityWriteResult:
        key = (snapshot.tenant_id, snapshot.room_id, snapshot.browser_pseudonym)
        with self._lock:
            current = self._rows.get(key)
            if current is not None and current.sequence == snapshot.sequence:
                if current.document_digest == snapshot.document_digest:
                    return _write("replayed", current, "sfu_capability_replayed")
                return _write("conflict", current, "sfu_capability_sequence_conflict")
            if current is not None and snapshot.sequence < current.sequence:
                return _write("conflict", current, "sfu_capability_replay")
            if (current is None and expected_version != 0) or (current is not None and current.version != expected_version):
                return _write("conflict", current, "sfu_capability_cas_conflict")
            active = sum(
                1 for value in self._rows.values()
                if value.tenant_id == snapshot.tenant_id and value.room_id == snapshot.room_id
                and value.expires_at_ms > now_ms and value.state in {"active", "unsupported"}
            )
            if current is None and active >= room_cardinality_max:
                return _write("capacity", None, "sfu_capability_room_cardinality_exceeded")
            saved = replace(snapshot, version=(current.version + 1 if current else 1))
            self._rows[key] = saved
            return _write("saved", saved, "sfu_capability_saved")

    def revoke(self, *, tenant_id: str, room_id: str, browser_pseudonym: str,
               expected_version: int, now_ms: int) -> SfuBrowserCapabilityWriteResult:
        key = (tenant_id, room_id, browser_pseudonym)
        with self._lock:
            current = self._rows.get(key)
            if current is None:
                return _write("replayed", None, "sfu_capability_already_absent")
            if current.version != expected_version:
                return _write("conflict", current, "sfu_capability_cas_conflict")
            saved = replace(current, state="stale", expires_at_ms=now_ms, version=current.version + 1)
            self._rows[key] = saved
            return _write("saved", saved, "sfu_capability_revoked")

    def purge(self, *, now_ms: int, limit: int) -> int:
        with self._lock:
            keys = [key for key, value in self._rows.items() if value.expires_at_ms + 900_000 <= now_ms][:limit]
            for key in keys:
                self._rows.pop(key, None)
            return len(keys)


class SqlSfuBrowserCapabilityRepository:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def read(self, *, tenant_id: str, room_id: str, browser_pseudonym: str,
             admission_epoch: int, membership_epoch: int, now_ms: int) -> SfuBrowserCapabilitySnapshot:
        try:
            with Session(self._engine) as db:
                row = _find(db, tenant_id, room_id, browser_pseudonym)
                if row is None:
                    return unknown_capability(
                        tenant_id=tenant_id, room_id=room_id, browser_pseudonym=browser_pseudonym,
                        admission_epoch=admission_epoch, membership_epoch=membership_epoch,
                    )
                snapshot = _snapshot(row)
                if row.status == "revoked" or row.expires_at_ms <= now_ms:
                    return replace(snapshot, state="stale")
                if row.admission_epoch != admission_epoch or row.membership_epoch != membership_epoch:
                    return replace(snapshot, state="stale")
                return snapshot
        except SQLAlchemyError as exc:
            raise SfuBrowserCapabilityRepositoryError("sfu_capability_store_unavailable") from exc

    def save(self, snapshot: SfuBrowserCapabilitySnapshot, *, expected_version: int,
             room_cardinality_max: int, now_ms: int) -> SfuBrowserCapabilityWriteResult:
        try:
            with Session(self._engine) as db:
                current = _find(db, snapshot.tenant_id, snapshot.room_id, snapshot.browser_pseudonym)
                if current is not None and current.sequence == snapshot.sequence:
                    state = _snapshot(current)
                    if current.document_digest == snapshot.document_digest:
                        return _write("replayed", state, "sfu_capability_replayed")
                    return _write("conflict", state, "sfu_capability_sequence_conflict")
                if current is not None and snapshot.sequence < current.sequence:
                    return _write("conflict", _snapshot(current), "sfu_capability_replay")
                if (current is None and expected_version != 0) or (current is not None and current.version != expected_version):
                    return _write("conflict", _snapshot(current) if current else None, "sfu_capability_cas_conflict")
                if current is None:
                    count = len(db.exec(select(SfuBrowserCapabilityDB).where(
                        SfuBrowserCapabilityDB.tenant_id == snapshot.tenant_id,
                        SfuBrowserCapabilityDB.room_id == snapshot.room_id,
                        SfuBrowserCapabilityDB.status.in_(("active", "unsupported")),
                        SfuBrowserCapabilityDB.expires_at_ms > now_ms,
                    ).limit(room_cardinality_max)).all())
                    if count >= room_cardinality_max:
                        return _write("capacity", None, "sfu_capability_room_cardinality_exceeded")
                    saved = replace(snapshot, version=1)
                    db.add(_row(saved, now_ms))
                else:
                    saved = replace(snapshot, version=current.version + 1)
                    result = db.exec(sa.update(SfuBrowserCapabilityDB).where(
                        SfuBrowserCapabilityDB.id == current.id,
                        SfuBrowserCapabilityDB.version == expected_version,
                    ).values(**_values(saved, now_ms)))
                    if int(result.rowcount or 0) != 1:
                        db.rollback()
                        return _write("conflict", _snapshot(current), "sfu_capability_cas_conflict")
                db.commit()
                return _write("saved", saved, "sfu_capability_saved")
        except IntegrityError:
            return _write("conflict", None, "sfu_capability_cas_conflict")
        except SQLAlchemyError as exc:
            raise SfuBrowserCapabilityRepositoryError("sfu_capability_store_unavailable") from exc

    def revoke(self, *, tenant_id: str, room_id: str, browser_pseudonym: str,
               expected_version: int, now_ms: int) -> SfuBrowserCapabilityWriteResult:
        try:
            with Session(self._engine) as db:
                row = _find(db, tenant_id, room_id, browser_pseudonym)
                if row is None:
                    return _write("replayed", None, "sfu_capability_already_absent")
                if row.version != expected_version:
                    return _write("conflict", _snapshot(row), "sfu_capability_cas_conflict")
                result = db.exec(sa.update(SfuBrowserCapabilityDB).where(
                    SfuBrowserCapabilityDB.id == row.id,
                    SfuBrowserCapabilityDB.version == expected_version,
                ).values(status="revoked", expires_at_ms=now_ms, version=row.version + 1, updated_at=now_ms / 1000.0))
                if int(result.rowcount or 0) != 1:
                    db.rollback()
                    return _write("conflict", _snapshot(row), "sfu_capability_cas_conflict")
                db.commit()
                return _write("saved", replace(_snapshot(row), state="stale", version=row.version + 1, expires_at_ms=now_ms), "sfu_capability_revoked")
        except SQLAlchemyError as exc:
            raise SfuBrowserCapabilityRepositoryError("sfu_capability_store_unavailable") from exc

    def purge(self, *, now_ms: int, limit: int) -> int:
        try:
            with Session(self._engine) as db:
                ids = [row.id for row in db.exec(select(SfuBrowserCapabilityDB).where(
                    SfuBrowserCapabilityDB.retain_until_ms <= now_ms,
                ).order_by(SfuBrowserCapabilityDB.retain_until_ms).limit(limit)).all()]
                if ids:
                    db.exec(sa.delete(SfuBrowserCapabilityDB).where(SfuBrowserCapabilityDB.id.in_(ids)))
                    db.commit()
                return len(ids)
        except SQLAlchemyError as exc:
            raise SfuBrowserCapabilityRepositoryError("sfu_capability_store_unavailable") from exc


def _find(db: Session, tenant: str, room: str, pseudonym: str) -> SfuBrowserCapabilityDB | None:
    return db.exec(select(SfuBrowserCapabilityDB).where(
        SfuBrowserCapabilityDB.tenant_id == tenant,
        SfuBrowserCapabilityDB.room_id == room,
        SfuBrowserCapabilityDB.browser_pseudonym == pseudonym,
    )).first()


def _snapshot(row: SfuBrowserCapabilityDB) -> SfuBrowserCapabilitySnapshot:
    state = "unsupported" if row.status == "unsupported" else "active"
    return SfuBrowserCapabilitySnapshot(
        row.tenant_id, row.room_id, row.browser_pseudonym, row.admission_epoch,
        row.membership_epoch, row.sequence, row.version, row.capability_version,
        row.capability_class, tuple(json.loads(json.dumps(row.buckets_json))), state,
        row.expires_at_ms, row.document_digest,
    )


def _row(value: SfuBrowserCapabilitySnapshot, now_ms: int) -> SfuBrowserCapabilityDB:
    return SfuBrowserCapabilityDB(id=_id(value), **_values(value, now_ms))


def _values(value: SfuBrowserCapabilitySnapshot, now_ms: int) -> dict:
    return {
        "tenant_id": value.tenant_id, "room_id": value.room_id,
        "browser_pseudonym": value.browser_pseudonym,
        "admission_epoch": value.admission_epoch, "membership_epoch": value.membership_epoch,
        "capability_version": value.capability_version, "schema_version": 1,
        "sequence": value.sequence, "capability_class": value.capability_class,
        "buckets_json": [dict(item) for item in value.buckets],
        "document_digest": value.document_digest,
        "status": "unsupported" if value.state == "unsupported" else "active",
        "version": value.version, "expires_at_ms": value.expires_at_ms,
        "retain_until_ms": value.expires_at_ms + 900_000, "updated_at": now_ms / 1000.0,
    }


def _id(value: SfuBrowserCapabilitySnapshot) -> str:
    import hashlib
    return "sfu-cap-" + hashlib.sha256(
        f"{value.tenant_id}\0{value.room_id}\0{value.browser_pseudonym}".encode()
    ).hexdigest()[:32]


def _write(status, snapshot, reason):
    return SfuBrowserCapabilityWriteResult(status, snapshot, reason)


__all__ = ["InMemorySfuBrowserCapabilityRepository", "SqlSfuBrowserCapabilityRepository", "SfuBrowserCapabilityRepositoryError"]
