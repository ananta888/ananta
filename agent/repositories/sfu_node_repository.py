"""SQL authority for the Hub-owned, multi-Hub SFU node directory."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SfuNodeDB, SfuNodeMutationDB


HEALTH_STATUSES = frozenset({"unknown", "healthy", "degraded", "unhealthy"})
DRAIN_STATES = frozenset({"active", "draining", "drained"})
MAX_PAGE_SIZE = 200


class SfuNodeRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuNodeRecord:
    id: str
    tenant_id: str
    cluster_id: str
    node_id: str
    runtime_identity_id: str
    enrollment_status: str
    region: str
    adapter_name: str
    adapter_version: str
    protocol_version: str
    capability_digest: str
    last_observation_id: str | None
    last_observed_at: float | None
    observation_expires_at: float | None
    health_status: str
    observation_status: str
    effective_health: str
    drain_state: str
    drain_reason: str | None
    drain_requested_at: float | None
    drained_at: float | None
    revoked_at: float | None
    revocation_reason: str | None
    fencing_token: int
    version: int
    created_at: float
    updated_at: float

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "cluster_id": self.cluster_id,
            "node_id": self.node_id,
            "runtime_identity_id": self.runtime_identity_id,
            "enrollment_status": self.enrollment_status,
            "region": self.region,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "protocol_version": self.protocol_version,
            "capability_digest": self.capability_digest,
            "last_observation_id": self.last_observation_id,
            "last_observed_at": self.last_observed_at,
            "observation_expires_at": self.observation_expires_at,
            "health_status": self.health_status,
            "observation_status": self.observation_status,
            "effective_health": self.effective_health,
            "drain_state": self.drain_state,
            "drain_reason": self.drain_reason,
            "drain_requested_at": self.drain_requested_at,
            "drained_at": self.drained_at,
            "revoked_at": self.revoked_at,
            "revocation_reason": self.revocation_reason,
            "fencing_token": self.fencing_token,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class SfuNodePage:
    items: tuple[SfuNodeRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SfuNodeChange:
    sequence: int
    event_type: str
    node: SfuNodeRecord
    occurred_at: float


@dataclass(frozen=True, slots=True)
class SfuNodeWatchPage:
    changes: tuple[SfuNodeChange, ...]
    cursor: str
    has_more: bool


class SfuNodeRepositoryPort(Protocol):
    def enroll_node(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        runtime_identity_id: str,
        region: str,
        adapter_name: str,
        adapter_version: str,
        protocol_version: str,
        capability_digest: str,
        expected_version: int,
        fencing_token: int = 0,
    ) -> SfuNodeRecord: ...

    def get_node(
        self, *, tenant_id: str, cluster_id: str, node_id: str
    ) -> SfuNodeRecord | None: ...

    def record_observation(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        observation_id: str,
        region: str,
        adapter_name: str,
        adapter_version: str,
        protocol_version: str,
        capability_digest: str,
        health_status: str,
        observation_ttl_seconds: float,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord: ...

    def set_drain(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        drain_state: str,
        reason: str,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord: ...

    def revoke_node(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        reason: str,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord: ...

    def mark_unknown(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        reason: str,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord: ...

    def list_nodes(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SfuNodePage: ...

    def watch_nodes(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SfuNodeWatchPage: ...


class SqlSfuNodeRepository:
    """Database-only node directory with CAS and monotonic fencing."""

    def __init__(
        self,
        *,
        cursor_signing_key: bytes,
        db_engine=default_engine,
        clock=time.time,
    ) -> None:
        if not isinstance(cursor_signing_key, bytes) or len(cursor_signing_key) < 16:
            raise ValueError("sfu_node_cursor_signing_key_too_short")
        self._engine = db_engine
        self._clock = clock
        self._cursors = _CursorCodec(cursor_signing_key)

    def enroll_node(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        runtime_identity_id: str,
        region: str,
        adapter_name: str,
        adapter_version: str,
        protocol_version: str,
        capability_digest: str,
        expected_version: int,
        fencing_token: int = 0,
    ) -> SfuNodeRecord:
        _validate_scope(tenant_id, cluster_id, node_id)
        for value, reason_code in (
            (runtime_identity_id, "sfu_node_runtime_identity_required"),
            (region, "sfu_node_region_required"),
            (adapter_name, "sfu_node_adapter_required"),
            (adapter_version, "sfu_node_adapter_version_required"),
            (protocol_version, "sfu_node_protocol_version_required"),
            (capability_digest, "sfu_node_capability_digest_required"),
        ):
            _require_text(value, reason_code)
        if expected_version != 0:
            raise SfuNodeRepositoryError("sfu_node_create_expected_version_invalid")
        _validate_fencing_token(fencing_token)
        now = float(self._clock())
        row = SfuNodeDB(
            id=f"sfu-node-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            runtime_identity_id=runtime_identity_id,
            enrollment_status="enrolled",
            region=region,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            protocol_version=protocol_version,
            capability_digest=capability_digest,
            health_status="unknown",
            drain_state="active",
            fencing_token=fencing_token,
            version=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with Session(self._engine) as db:
                db.add(row)
                db.flush()
                db.add(_mutation_from_row(row, event_type="enrolled", occurred_at=now))
                db.commit()
                return _record_from_row(row, now=now)
        except IntegrityError as exc:
            raise SfuNodeRepositoryError("sfu_node_identity_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuNodeRepositoryError("sfu_node_store_unavailable") from exc

    def get_node(
        self, *, tenant_id: str, cluster_id: str, node_id: str
    ) -> SfuNodeRecord | None:
        _validate_scope(tenant_id, cluster_id, node_id)
        try:
            with Session(self._engine) as db:
                row = _scoped_row(
                    db,
                    tenant_id=tenant_id,
                    cluster_id=cluster_id,
                    node_id=node_id,
                )
                return None if row is None else _record_from_row(row, now=float(self._clock()))
        except SQLAlchemyError as exc:
            raise SfuNodeRepositoryError("sfu_node_store_unavailable") from exc

    def record_observation(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        observation_id: str,
        region: str,
        adapter_name: str,
        adapter_version: str,
        protocol_version: str,
        capability_digest: str,
        health_status: str,
        observation_ttl_seconds: float,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord:
        _validate_scope(tenant_id, cluster_id, node_id)
        for value, reason_code in (
            (observation_id, "sfu_node_observation_id_required"),
            (region, "sfu_node_region_required"),
            (adapter_name, "sfu_node_adapter_required"),
            (adapter_version, "sfu_node_adapter_version_required"),
            (protocol_version, "sfu_node_protocol_version_required"),
            (capability_digest, "sfu_node_capability_digest_required"),
        ):
            _require_text(value, reason_code)
        if health_status not in HEALTH_STATUSES or health_status == "unknown":
            raise SfuNodeRepositoryError("sfu_node_health_status_invalid")
        if observation_ttl_seconds <= 0:
            raise SfuNodeRepositoryError("sfu_node_observation_ttl_invalid")
        now = float(self._clock())
        return self._cas_update(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            expected_version=expected_version,
            fencing_token=fencing_token,
            event_type="observed",
            values={
                "region": region,
                "adapter_name": adapter_name,
                "adapter_version": adapter_version,
                "protocol_version": protocol_version,
                "capability_digest": capability_digest,
                "last_observation_id": observation_id,
                "last_observed_at": now,
                "observation_expires_at": now + float(observation_ttl_seconds),
                "health_status": health_status,
            },
            now=now,
        )

    def set_drain(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        drain_state: str,
        reason: str,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord:
        _validate_scope(tenant_id, cluster_id, node_id)
        if drain_state not in DRAIN_STATES:
            raise SfuNodeRepositoryError("sfu_node_drain_state_invalid")
        _require_text(reason, "sfu_node_drain_reason_required")
        now = float(self._clock())
        values: dict[str, object] = {
            "drain_state": drain_state,
            "drain_reason": reason,
        }
        if drain_state == "active":
            values.update(drain_requested_at=None, drained_at=None)
        elif drain_state == "draining":
            values.update(drain_requested_at=now, drained_at=None)
        else:
            values.update(drained_at=now)
        return self._cas_update(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            expected_version=expected_version,
            fencing_token=fencing_token,
            event_type=f"drain_{drain_state}",
            values=values,
            now=now,
        )

    def revoke_node(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        reason: str,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord:
        _validate_scope(tenant_id, cluster_id, node_id)
        _require_text(reason, "sfu_node_revocation_reason_required")
        now = float(self._clock())
        return self._cas_update(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            expected_version=expected_version,
            fencing_token=fencing_token,
            event_type="revoked",
            values={
                "enrollment_status": "revoked",
                "revoked_at": now,
                "revocation_reason": reason,
                "drain_state": "drained",
                "drained_at": now,
            },
            now=now,
        )

    def mark_unknown(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        reason: str,
        expected_version: int,
        fencing_token: int,
    ) -> SfuNodeRecord:
        _validate_scope(tenant_id, cluster_id, node_id)
        _require_text(reason, "sfu_node_unknown_reason_required")
        now = float(self._clock())
        return self._cas_update(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            node_id=node_id,
            expected_version=expected_version,
            fencing_token=fencing_token,
            event_type="health_unknown",
            values={
                "health_status": "unknown",
                "observation_expires_at": now,
            },
            now=now,
        )

    def list_nodes(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SfuNodePage:
        _validate_scope(tenant_id, cluster_id)
        _validate_page_size(limit)
        after_node_id = ""
        after_id = ""
        if cursor is not None:
            position = self._decode_cursor(
                cursor,
                kind="list",
                tenant_id=tenant_id,
                cluster_id=cluster_id,
            )
            after_node_id = _cursor_text(position, "node_id")
            after_id = _cursor_text(position, "id")
        try:
            with Session(self._engine) as db:
                statement = (
                    select(SfuNodeDB)
                    .where(
                        SfuNodeDB.tenant_id == tenant_id,
                        SfuNodeDB.cluster_id == cluster_id,
                    )
                    .order_by(SfuNodeDB.node_id.asc(), SfuNodeDB.id.asc())
                    .limit(limit + 1)
                )
                if cursor is not None:
                    statement = statement.where(
                        sa.or_(
                            SfuNodeDB.node_id > after_node_id,
                            sa.and_(
                                SfuNodeDB.node_id == after_node_id,
                                SfuNodeDB.id > after_id,
                            ),
                        )
                    )
                rows = tuple(db.exec(statement).all())
        except SQLAlchemyError as exc:
            raise SfuNodeRepositoryError("sfu_node_store_unavailable") from exc
        selected = rows[:limit]
        now = float(self._clock())
        items = tuple(_record_from_row(row, now=now) for row in selected)
        next_cursor = None
        if len(rows) > limit and selected:
            last = selected[-1]
            next_cursor = self._encode_cursor(
                kind="list",
                tenant_id=tenant_id,
                cluster_id=cluster_id,
                position={"node_id": last.node_id, "id": last.id},
            )
        return SfuNodePage(items=items, next_cursor=next_cursor)

    def watch_nodes(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SfuNodeWatchPage:
        _validate_scope(tenant_id, cluster_id)
        _validate_page_size(limit)
        after_sequence = 0
        if cursor is not None:
            position = self._decode_cursor(
                cursor,
                kind="watch",
                tenant_id=tenant_id,
                cluster_id=cluster_id,
            )
            after_sequence = _cursor_integer(position, "sequence")
        try:
            with Session(self._engine) as db:
                rows = tuple(
                    db.exec(
                        select(SfuNodeMutationDB)
                        .where(
                            SfuNodeMutationDB.tenant_id == tenant_id,
                            SfuNodeMutationDB.cluster_id == cluster_id,
                            SfuNodeMutationDB.sequence > after_sequence,
                        )
                        .order_by(SfuNodeMutationDB.sequence.asc())
                        .limit(limit + 1)
                    ).all()
                )
        except SQLAlchemyError as exc:
            raise SfuNodeRepositoryError("sfu_node_store_unavailable") from exc
        selected = rows[:limit]
        now = float(self._clock())
        changes = tuple(
            SfuNodeChange(
                sequence=_required_sequence(row.sequence),
                event_type=row.event_type,
                node=_record_from_snapshot(row.snapshot_json, now=now),
                occurred_at=row.occurred_at,
            )
            for row in selected
        )
        last_sequence = changes[-1].sequence if changes else after_sequence
        next_cursor = self._encode_cursor(
            kind="watch",
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            position={"sequence": last_sequence},
        )
        return SfuNodeWatchPage(
            changes=changes,
            cursor=next_cursor,
            has_more=len(rows) > limit,
        )

    def _cas_update(
        self,
        *,
        tenant_id: str,
        cluster_id: str,
        node_id: str,
        expected_version: int,
        fencing_token: int,
        event_type: str,
        values: dict[str, object],
        now: float,
    ) -> SfuNodeRecord:
        if expected_version <= 0:
            raise SfuNodeRepositoryError("sfu_node_expected_version_invalid")
        _validate_fencing_token(fencing_token)
        try:
            with Session(self._engine) as db:
                current = _scoped_row(
                    db,
                    tenant_id=tenant_id,
                    cluster_id=cluster_id,
                    node_id=node_id,
                )
                if current is None:
                    raise SfuNodeRepositoryError("sfu_node_not_found")
                if current.revoked_at is not None:
                    raise SfuNodeRepositoryError("sfu_node_revoked")
                if current.version != expected_version:
                    raise SfuNodeRepositoryError("sfu_node_version_conflict")
                if fencing_token < current.fencing_token:
                    raise SfuNodeRepositoryError("sfu_node_fencing_conflict")
                update_values = dict(values)
                update_values.update(
                    fencing_token=fencing_token,
                    version=expected_version + 1,
                    updated_at=now,
                )
                result = db.exec(
                    sa.update(SfuNodeDB)
                    .where(
                        SfuNodeDB.id == current.id,
                        SfuNodeDB.tenant_id == tenant_id,
                        SfuNodeDB.cluster_id == cluster_id,
                        SfuNodeDB.version == expected_version,
                        SfuNodeDB.fencing_token <= fencing_token,
                        SfuNodeDB.revoked_at.is_(None),
                    )
                    .values(**update_values)
                )
                if result.rowcount != 1:
                    db.rollback()
                    raise SfuNodeRepositoryError("sfu_node_version_conflict")
                db.flush()
                db.expire_all()
                updated = db.get(SfuNodeDB, current.id)
                if updated is None:
                    raise SfuNodeRepositoryError("sfu_node_not_found")
                db.add(_mutation_from_row(updated, event_type=event_type, occurred_at=now))
                db.commit()
                return _record_from_row(updated, now=now)
        except SfuNodeRepositoryError:
            raise
        except IntegrityError as exc:
            raise SfuNodeRepositoryError("sfu_node_mutation_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuNodeRepositoryError("sfu_node_store_unavailable") from exc

    def _encode_cursor(
        self,
        *,
        kind: str,
        tenant_id: str,
        cluster_id: str,
        position: dict[str, object],
    ) -> str:
        return self._cursors.encode(
            {
                "v": 1,
                "kind": kind,
                "tenant_id": tenant_id,
                "cluster_id": cluster_id,
                "position": position,
            }
        )

    def _decode_cursor(
        self,
        cursor: str,
        *,
        kind: str,
        tenant_id: str,
        cluster_id: str,
    ) -> dict[str, object]:
        payload = self._cursors.decode(cursor)
        if payload.get("v") != 1 or payload.get("kind") != kind:
            raise SfuNodeRepositoryError("sfu_node_cursor_invalid")
        if payload.get("tenant_id") != tenant_id or payload.get("cluster_id") != cluster_id:
            raise SfuNodeRepositoryError("sfu_node_cursor_scope_mismatch")
        position = payload.get("position")
        if not isinstance(position, dict):
            raise SfuNodeRepositoryError("sfu_node_cursor_invalid")
        return position


class _CursorCodec:
    def __init__(self, signing_key: bytes) -> None:
        self._signing_key = signing_key

    def encode(self, payload: dict[str, object]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._signing_key, raw, hashlib.sha256).digest()
        return f"{_base64url_encode(raw)}.{_base64url_encode(signature)}"

    def decode(self, token: str) -> dict[str, object]:
        try:
            raw_part, signature_part = token.split(".", 1)
            raw = _base64url_decode(raw_part)
            supplied_signature = _base64url_decode(signature_part)
            expected_signature = hmac.new(self._signing_key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise SfuNodeRepositoryError("sfu_node_cursor_invalid")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise SfuNodeRepositoryError("sfu_node_cursor_invalid")
            return payload
        except SfuNodeRepositoryError:
            raise
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise SfuNodeRepositoryError("sfu_node_cursor_invalid") from exc


def _scoped_row(
    db: Session,
    *,
    tenant_id: str,
    cluster_id: str,
    node_id: str,
) -> SfuNodeDB | None:
    return db.exec(
        select(SfuNodeDB).where(
            SfuNodeDB.tenant_id == tenant_id,
            SfuNodeDB.cluster_id == cluster_id,
            SfuNodeDB.node_id == node_id,
        )
    ).first()


def _snapshot_from_row(row: SfuNodeDB) -> dict[str, object]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "cluster_id": row.cluster_id,
        "node_id": row.node_id,
        "runtime_identity_id": row.runtime_identity_id,
        "enrollment_status": row.enrollment_status,
        "region": row.region,
        "adapter_name": row.adapter_name,
        "adapter_version": row.adapter_version,
        "protocol_version": row.protocol_version,
        "capability_digest": row.capability_digest,
        "last_observation_id": row.last_observation_id,
        "last_observed_at": row.last_observed_at,
        "observation_expires_at": row.observation_expires_at,
        "health_status": row.health_status,
        "drain_state": row.drain_state,
        "drain_reason": row.drain_reason,
        "drain_requested_at": row.drain_requested_at,
        "drained_at": row.drained_at,
        "revoked_at": row.revoked_at,
        "revocation_reason": row.revocation_reason,
        "fencing_token": row.fencing_token,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _record_from_row(row: SfuNodeDB, *, now: float) -> SfuNodeRecord:
    return _record_from_snapshot(_snapshot_from_row(row), now=now)


def _record_from_snapshot(snapshot: dict, *, now: float) -> SfuNodeRecord:
    expires_at = _optional_float(snapshot.get("observation_expires_at"))
    observed_at = _optional_float(snapshot.get("last_observed_at"))
    health_status = str(snapshot["health_status"])
    if observed_at is None or expires_at is None:
        observation_status = "unknown"
    elif now >= expires_at:
        observation_status = "stale"
    else:
        observation_status = "current"
    effective_health = health_status if observation_status == "current" else "unknown"
    return SfuNodeRecord(
        id=str(snapshot["id"]),
        tenant_id=str(snapshot["tenant_id"]),
        cluster_id=str(snapshot["cluster_id"]),
        node_id=str(snapshot["node_id"]),
        runtime_identity_id=str(snapshot["runtime_identity_id"]),
        enrollment_status=str(snapshot["enrollment_status"]),
        region=str(snapshot["region"]),
        adapter_name=str(snapshot["adapter_name"]),
        adapter_version=str(snapshot["adapter_version"]),
        protocol_version=str(snapshot["protocol_version"]),
        capability_digest=str(snapshot["capability_digest"]),
        last_observation_id=_optional_text(snapshot.get("last_observation_id")),
        last_observed_at=observed_at,
        observation_expires_at=expires_at,
        health_status=health_status,
        observation_status=observation_status,
        effective_health=effective_health,
        drain_state=str(snapshot["drain_state"]),
        drain_reason=_optional_text(snapshot.get("drain_reason")),
        drain_requested_at=_optional_float(snapshot.get("drain_requested_at")),
        drained_at=_optional_float(snapshot.get("drained_at")),
        revoked_at=_optional_float(snapshot.get("revoked_at")),
        revocation_reason=_optional_text(snapshot.get("revocation_reason")),
        fencing_token=int(snapshot["fencing_token"]),
        version=int(snapshot["version"]),
        created_at=float(snapshot["created_at"]),
        updated_at=float(snapshot["updated_at"]),
    )


def _mutation_from_row(
    row: SfuNodeDB, *, event_type: str, occurred_at: float
) -> SfuNodeMutationDB:
    return SfuNodeMutationDB(
        tenant_id=row.tenant_id,
        cluster_id=row.cluster_id,
        node_id=row.node_id,
        node_version=row.version,
        fencing_token=row.fencing_token,
        event_type=event_type,
        snapshot_json=_snapshot_from_row(row),
        occurred_at=occurred_at,
    )


def _validate_scope(tenant_id: str, cluster_id: str, node_id: str | None = None) -> None:
    _require_text(tenant_id, "sfu_node_tenant_required")
    _require_text(cluster_id, "sfu_node_cluster_required")
    if node_id is not None:
        _require_text(node_id, "sfu_node_id_required")


def _require_text(value: str, reason_code: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SfuNodeRepositoryError(reason_code)


def _validate_fencing_token(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SfuNodeRepositoryError("sfu_node_fencing_token_invalid")


def _validate_page_size(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGE_SIZE:
        raise SfuNodeRepositoryError("sfu_node_page_size_invalid")


def _cursor_text(position: dict[str, object], name: str) -> str:
    value = position.get(name)
    if not isinstance(value, str):
        raise SfuNodeRepositoryError("sfu_node_cursor_invalid")
    return value


def _cursor_integer(position: dict[str, object], name: str) -> int:
    value = position.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SfuNodeRepositoryError("sfu_node_cursor_invalid")
    return value


def _required_sequence(value: int | None) -> int:
    if value is None:
        raise SfuNodeRepositoryError("sfu_node_change_sequence_missing")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = [
    "SfuNodeChange",
    "SfuNodePage",
    "SfuNodeRecord",
    "SfuNodeRepositoryError",
    "SfuNodeRepositoryPort",
    "SfuNodeWatchPage",
    "SqlSfuNodeRepository",
]
