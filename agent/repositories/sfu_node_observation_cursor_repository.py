"""Durable replay, ordering and freshness boundary for SFU observations."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SfuNodeObservationCursorDB, SfuNodeObservationReplayDB


class SfuNodeObservationCursorError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuNodeObservationAcceptance:
    status: str
    receipt_id: str
    observation_id: str
    normalized_observation: dict[str, object]
    applied_node_version: int | None
    cursor_version: int
    highest_sequence: int


class SfuNodeObservationCursorRepositoryPort(Protocol):
    def accept_observation(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        region: str,
        node_id: str | None,
        producer_mode: str,
        producer_id: str,
        boot_id: str,
        sequence: int,
        measured_at: float,
        fresh_until: float,
        payload_digest: str,
        normalized_observation: Mapping[str, object],
        fencing_token: int,
        sequence_window: int,
        entries_max: int,
        cursor_ttl_seconds: int,
        retention_seconds: int,
        boot_sequence_start_max: int,
    ) -> SfuNodeObservationAcceptance: ...

    def mark_applied(self, *, receipt_id: str, node_version: int) -> None: ...


class SqlSfuNodeObservationCursorRepository:
    """SQL-only cursor store; replay state never falls back to process memory."""

    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def accept_observation(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        region: str,
        node_id: str | None,
        producer_mode: str,
        producer_id: str,
        boot_id: str,
        sequence: int,
        measured_at: float,
        fresh_until: float,
        payload_digest: str,
        normalized_observation: Mapping[str, object],
        fencing_token: int,
        sequence_window: int,
        entries_max: int,
        cursor_ttl_seconds: int,
        retention_seconds: int,
        boot_sequence_start_max: int,
    ) -> SfuNodeObservationAcceptance:
        _validate_inputs(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            region=region,
            node_id=node_id,
            producer_mode=producer_mode,
            producer_id=producer_id,
            boot_id=boot_id,
            sequence=sequence,
            fencing_token=fencing_token,
            sequence_window=sequence_window,
            entries_max=entries_max,
            cursor_ttl_seconds=cursor_ttl_seconds,
            retention_seconds=retention_seconds,
            boot_sequence_start_max=boot_sequence_start_max,
        )
        normalized = _bounded_json_copy(normalized_observation)
        now = float(self._clock())
        cursor_id = _cursor_id(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            producer_mode=producer_mode,
        )
        for attempt in range(3):
            try:
                return self._accept_once(
                    cursor_id=cursor_id,
                    tenant_id=tenant_id,
                    cluster_id=cluster_id,
                    region=region,
                    node_id=node_id,
                    producer_mode=producer_mode,
                    producer_id=producer_id,
                    boot_id=boot_id,
                    sequence=sequence,
                    measured_at=measured_at,
                    fresh_until=fresh_until,
                    payload_digest=payload_digest,
                    normalized=normalized,
                    fencing_token=fencing_token,
                    sequence_window=sequence_window,
                    entries_max=entries_max,
                    cursor_ttl_seconds=cursor_ttl_seconds,
                    retention_seconds=retention_seconds,
                    boot_sequence_start_max=boot_sequence_start_max,
                    now=now,
                )
            except IntegrityError as exc:
                if attempt == 2:
                    raise SfuNodeObservationCursorError(
                        "sfu_observation_cursor_cas_conflict"
                    ) from exc
            except SfuNodeObservationCursorError:
                raise
            except SQLAlchemyError as exc:
                raise SfuNodeObservationCursorError(
                    "sfu_observation_cursor_store_unavailable"
                ) from exc
        raise SfuNodeObservationCursorError("sfu_observation_cursor_cas_conflict")

    def _accept_once(
        self,
        *,
        cursor_id: str,
        tenant_id: str,
        cluster_id: str,
        region: str,
        node_id: str | None,
        producer_mode: str,
        producer_id: str,
        boot_id: str,
        sequence: int,
        measured_at: float,
        fresh_until: float,
        payload_digest: str,
        normalized: dict[str, object],
        fencing_token: int,
        sequence_window: int,
        entries_max: int,
        cursor_ttl_seconds: int,
        retention_seconds: int,
        boot_sequence_start_max: int,
        now: float,
    ) -> SfuNodeObservationAcceptance:
        observation_id = f"sha256:{payload_digest}"
        with Session(self._engine) as db:
            cursor = db.get(SfuNodeObservationCursorDB, cursor_id)
            if cursor is not None:
                replay = db.exec(
                    select(SfuNodeObservationReplayDB).where(
                        SfuNodeObservationReplayDB.cursor_id == cursor_id,
                        SfuNodeObservationReplayDB.boot_id == boot_id,
                        SfuNodeObservationReplayDB.sequence == sequence,
                        SfuNodeObservationReplayDB.expires_at > now,
                    )
                ).first()
                if replay is not None:
                    if replay.payload_digest != payload_digest:
                        raise SfuNodeObservationCursorError(
                            "sfu_observation_sequence_payload_conflict"
                        )
                    return _duplicate_acceptance(cursor, replay)

            if now >= fresh_until:
                raise SfuNodeObservationCursorError("sfu_observation_stale")
            if cursor is None:
                cursor = SfuNodeObservationCursorDB(
                    id=cursor_id,
                    tenant_id=tenant_id,
                    cluster_id=cluster_id,
                    region=region,
                    node_id=node_id,
                    subject_key=_subject_key(node_id),
                    producer_mode=producer_mode,
                    producer_id=producer_id,
                    current_boot_id=boot_id,
                    retired_boot_ids=[],
                    highest_sequence=sequence,
                    last_payload_digest=payload_digest,
                    last_observation_id=observation_id,
                    last_measured_at=measured_at,
                    last_fresh_until=fresh_until,
                    normalized_observation_json=normalized,
                    fencing_token=fencing_token,
                    version=1,
                    entries_max=entries_max,
                    ttl_seconds=cursor_ttl_seconds,
                    retention_seconds=retention_seconds,
                    expires_at=now + cursor_ttl_seconds,
                    retain_until=now + cursor_ttl_seconds + retention_seconds,
                    created_at=now,
                    updated_at=now,
                )
                db.add(cursor)
                db.flush()
                receipt = _new_receipt(
                    cursor_id=cursor_id,
                    boot_id=boot_id,
                    sequence=sequence,
                    payload_digest=payload_digest,
                    observation_id=observation_id,
                    normalized=normalized,
                    status="accepted",
                    fresh_until=fresh_until,
                    now=now,
                    retention_seconds=retention_seconds,
                )
                db.add(receipt)
                db.flush()
                db.commit()
                return SfuNodeObservationAcceptance(
                    status="accepted",
                    receipt_id=receipt.id,
                    observation_id=observation_id,
                    normalized_observation=normalized,
                    applied_node_version=None,
                    cursor_version=1,
                    highest_sequence=sequence,
                )

            if cursor.producer_id != producer_id:
                raise SfuNodeObservationCursorError("sfu_observation_producer_changed")
            if fencing_token < cursor.fencing_token:
                raise SfuNodeObservationCursorError("sfu_observation_fencing_stale")

            boot_changed = boot_id != cursor.current_boot_id
            retired_boot_ids = tuple(str(value) for value in cursor.retired_boot_ids)
            if boot_changed:
                if boot_id in retired_boot_ids:
                    raise SfuNodeObservationCursorError("sfu_observation_boot_replay")
                if sequence > boot_sequence_start_max:
                    raise SfuNodeObservationCursorError(
                        "sfu_observation_boot_sequence_invalid"
                    )
                retired_boot_ids = (*retired_boot_ids, cursor.current_boot_id)[-entries_max:]
                acceptance_status = "accepted"
                next_highest = sequence
            elif sequence > cursor.highest_sequence:
                acceptance_status = "accepted"
                next_highest = sequence
            elif sequence >= max(0, cursor.highest_sequence - sequence_window + 1):
                acceptance_status = "accepted_reordered"
                next_highest = cursor.highest_sequence
            else:
                raise SfuNodeObservationCursorError(
                    "sfu_observation_sequence_outside_window"
                )

            next_version = cursor.version + 1
            values: dict[str, object] = {
                "region": region,
                "fencing_token": fencing_token,
                "version": next_version,
                "entries_max": entries_max,
                "ttl_seconds": cursor_ttl_seconds,
                "retention_seconds": retention_seconds,
                "expires_at": now + cursor_ttl_seconds,
                "retain_until": now + cursor_ttl_seconds + retention_seconds,
                "updated_at": now,
            }
            if acceptance_status == "accepted":
                values.update(
                    current_boot_id=boot_id,
                    retired_boot_ids=list(retired_boot_ids),
                    highest_sequence=next_highest,
                    last_payload_digest=payload_digest,
                    last_observation_id=observation_id,
                    last_measured_at=measured_at,
                    last_fresh_until=fresh_until,
                    normalized_observation_json=normalized,
                )
            updated = db.exec(
                sa.update(SfuNodeObservationCursorDB)
                .where(
                    SfuNodeObservationCursorDB.id == cursor_id,
                    SfuNodeObservationCursorDB.version == cursor.version,
                    SfuNodeObservationCursorDB.fencing_token <= fencing_token,
                )
                .values(**values)
            )
            if updated.rowcount != 1:
                db.rollback()
                raise IntegrityError("cursor CAS conflict", params=None, orig=None)
            receipt = _new_receipt(
                cursor_id=cursor_id,
                boot_id=boot_id,
                sequence=sequence,
                payload_digest=payload_digest,
                observation_id=observation_id,
                normalized=normalized,
                status=acceptance_status,
                fresh_until=fresh_until,
                now=now,
                retention_seconds=retention_seconds,
            )
            db.add(receipt)
            db.flush()
            _trim_replays(db, cursor_id=cursor_id, entries_max=entries_max, now=now)
            db.commit()
            return SfuNodeObservationAcceptance(
                status=acceptance_status,
                receipt_id=receipt.id,
                observation_id=observation_id,
                normalized_observation=normalized,
                applied_node_version=None,
                cursor_version=next_version,
                highest_sequence=next_highest,
            )

    def mark_applied(self, *, receipt_id: str, node_version: int) -> None:
        if not receipt_id or node_version <= 0:
            raise SfuNodeObservationCursorError("sfu_observation_applied_version_invalid")
        try:
            with Session(self._engine) as db:
                receipt = db.get(SfuNodeObservationReplayDB, receipt_id)
                if receipt is None:
                    raise SfuNodeObservationCursorError(
                        "sfu_observation_receipt_not_found"
                    )
                if receipt.applied_node_version is not None:
                    if receipt.applied_node_version != node_version:
                        raise SfuNodeObservationCursorError(
                            "sfu_observation_applied_version_conflict"
                        )
                    return
                updated = db.exec(
                    sa.update(SfuNodeObservationReplayDB)
                    .where(
                        SfuNodeObservationReplayDB.id == receipt_id,
                        SfuNodeObservationReplayDB.applied_node_version.is_(None),
                    )
                    .values(applied_node_version=node_version)
                )
                if updated.rowcount != 1:
                    db.rollback()
                    raise SfuNodeObservationCursorError(
                        "sfu_observation_receipt_cas_conflict"
                    )
                db.commit()
        except SfuNodeObservationCursorError:
            raise
        except SQLAlchemyError as exc:
            raise SfuNodeObservationCursorError(
                "sfu_observation_cursor_store_unavailable"
            ) from exc


def _duplicate_acceptance(
    cursor: SfuNodeObservationCursorDB,
    replay: SfuNodeObservationReplayDB,
) -> SfuNodeObservationAcceptance:
    return SfuNodeObservationAcceptance(
        status="duplicate",
        receipt_id=replay.id,
        observation_id=replay.observation_id,
        normalized_observation=dict(replay.normalized_observation_json),
        applied_node_version=replay.applied_node_version,
        cursor_version=cursor.version,
        highest_sequence=cursor.highest_sequence,
    )


def _new_receipt(
    *,
    cursor_id: str,
    boot_id: str,
    sequence: int,
    payload_digest: str,
    observation_id: str,
    normalized: dict[str, object],
    status: str,
    fresh_until: float,
    now: float,
    retention_seconds: int,
) -> SfuNodeObservationReplayDB:
    return SfuNodeObservationReplayDB(
        cursor_id=cursor_id,
        boot_id=boot_id,
        sequence=sequence,
        payload_digest=payload_digest,
        observation_id=observation_id,
        normalized_observation_json=normalized,
        acceptance_status=status,
        fresh_until=fresh_until,
        accepted_at=now,
        expires_at=now + retention_seconds,
    )


def _trim_replays(db: Session, *, cursor_id: str, entries_max: int, now: float) -> None:
    db.exec(
        sa.delete(SfuNodeObservationReplayDB).where(
            SfuNodeObservationReplayDB.cursor_id == cursor_id,
            SfuNodeObservationReplayDB.expires_at <= now,
        )
    )
    stale_ids = tuple(
        db.exec(
            select(SfuNodeObservationReplayDB.id)
            .where(SfuNodeObservationReplayDB.cursor_id == cursor_id)
            .order_by(
                SfuNodeObservationReplayDB.accepted_at.desc(),
                SfuNodeObservationReplayDB.id.desc(),
            )
            .offset(entries_max)
        ).all()
    )
    if stale_ids:
        db.exec(
            sa.delete(SfuNodeObservationReplayDB).where(
                SfuNodeObservationReplayDB.id.in_(stale_ids)
            )
        )


def _validate_inputs(**values: object) -> None:
    for name in (
        "tenant_id",
        "cluster_id",
        "region",
        "producer_mode",
        "producer_id",
        "boot_id",
    ):
        value = values[name]
        if not isinstance(value, str) or not value.strip():
            raise SfuNodeObservationCursorError(f"sfu_observation_{name}_invalid")
    node_id = values["node_id"]
    if node_id is not None and (not isinstance(node_id, str) or not node_id.strip()):
        raise SfuNodeObservationCursorError("sfu_observation_node_id_invalid")
    for name in (
        "sequence",
        "fencing_token",
        "sequence_window",
        "entries_max",
        "cursor_ttl_seconds",
        "retention_seconds",
        "boot_sequence_start_max",
    ):
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SfuNodeObservationCursorError(f"sfu_observation_{name}_invalid")
    if values["sequence_window"] < 1 or values["entries_max"] < values["sequence_window"]:
        raise SfuNodeObservationCursorError("sfu_observation_cursor_bounds_invalid")
    if values["entries_max"] > 10_000:
        raise SfuNodeObservationCursorError("sfu_observation_entries_max_invalid")
    if values["cursor_ttl_seconds"] < 1 or values["retention_seconds"] < 1:
        raise SfuNodeObservationCursorError("sfu_observation_cursor_ttl_invalid")


def _cursor_id(
    *, tenant_id: str, cluster_id: str, node_id: str | None, producer_mode: str
) -> str:
    material = json.dumps(
        [tenant_id, cluster_id, _subject_key(node_id), producer_mode],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sfu-observation-cursor-" + hashlib.sha256(material).hexdigest()


def _subject_key(node_id: str | None) -> str:
    return f"node:{node_id}" if node_id is not None else "cluster"


def _bounded_json_copy(value: Mapping[str, object]) -> dict[str, object]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SfuNodeObservationCursorError(
            "sfu_observation_normalized_payload_invalid"
        ) from exc
    if len(encoded) > 32_768:
        raise SfuNodeObservationCursorError(
            "sfu_observation_normalized_payload_oversize"
        )
    decoded = json.loads(encoded.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise SfuNodeObservationCursorError(
            "sfu_observation_normalized_payload_invalid"
        )
    return decoded


__all__ = [
    "SfuNodeObservationAcceptance",
    "SfuNodeObservationCursorError",
    "SfuNodeObservationCursorRepositoryPort",
    "SqlSfuNodeObservationCursorRepository",
]
