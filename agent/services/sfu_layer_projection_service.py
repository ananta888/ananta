"""Hub policy projection materialization, scoped reads, and bounded receipts."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

from agent.repositories.sfu_layer_projection_repository import (
    ProjectionKind,
    SfuLayerProjectionRepositoryPort,
    SfuStoredLayerProjection,
    SfuStoredProjectionReceipt,
)
from agent.services.sfu_projection_signing import (
    HmacSfuProjectionSigner,
    SfuProjectionSignerPort,
)


_SCHEMAS = {
    "room": "ananta.sfu-room-session-projection.v1",
    "publisher": "ananta.sfu-publisher-layer-projection.v1",
    "receiver": "ananta.sfu-receiver-layer-projection.v1",
}
_TTL_MAX = {"room": 30_000, "publisher": 15_000, "receiver": 10_000}


@dataclass(frozen=True, slots=True)
class SfuProjectionScope:
    tenant_id: str
    room_id: str
    actor_id: str
    membership_epoch: int
    route_epoch: int = 0
    topology_epoch: int = 0
    key_epoch: int = 0


class SfuProjectionScopeAuthorizerPort(Protocol):
    def authorize(self, *, tenant_id: str, room_id: str, actor_id: str,
                  projection_kind: ProjectionKind, subject_ref: str) -> SfuProjectionScope | None: ...


@dataclass(frozen=True, slots=True)
class SfuProjectionMaterializeCommand:
    tenant_id: str
    room_id: str
    projection_kind: ProjectionKind
    subject_ref: str
    document: Mapping[str, object]


class SfuLayerProjectionError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 400, retry_after_seconds: int | None = None) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason_code)


class SfuLayerProjectionService:
    def __init__(self, repository: SfuLayerProjectionRepositoryPort,
                 signer: SfuProjectionSignerPort, *, clock: Callable[[], float] = time.time,
                 control_observer: SfuBroadcastControlObservationPort | None = None) -> None:
        self._repository = repository
        self._signer = signer
        self._clock = clock
        self._control_observer = control_observer_or_null(control_observer)
        self._rate_lock = threading.Lock()
        self._reads: dict[tuple[str, str, str], list[int]] = {}

    @observed_control_path("layer_projection")
    def materialize(self, command: SfuProjectionMaterializeCommand) -> SfuStoredLayerProjection:
        now_ms = int(self._clock() * 1000)
        document = json.loads(json.dumps(command.document))
        encoded = _canonical(document)
        if len(encoded) > 8192:
            raise SfuLayerProjectionError("sfu_projection_bytes_exceeded", 413)
        _validate_projection(command, document, now_ms)
        version = _positive(document.get("projection_version"), "projection_version")
        session_version = _positive(document.get("session_projection_version", version), "session_projection_version")
        fencing = document.get("fencing")
        if not isinstance(fencing, dict):
            raise SfuLayerProjectionError("sfu_projection_fencing_invalid")
        expected = _nonnegative(fencing.get("expected_previous_projection_version"), "expected_previous_projection_version")
        token = _positive(fencing.get("fencing_token"), "fencing_token")
        digest = hashlib.sha256(encoded).hexdigest()
        signed = self._signer.sign(digest)
        projection_ref = str(document.get("projection_ref") or f"{command.projection_kind}:{command.room_id}:{command.subject_ref}:{version}")
        expires = _utc_ms(document.get("expires_at"))
        projection = SfuStoredLayerProjection(
            _projection_id(command), command.tenant_id, command.room_id,
            command.projection_kind, command.subject_ref, projection_ref, version,
            session_version, _positive(document.get("membership_epoch"), "membership_epoch"),
            _optional_epoch(document, "route_epoch"), _optional_epoch(document, "topology_epoch"),
            _optional_epoch(document, "key_epoch"), token, expected, document, digest,
            signed.value, signed.key_id, signed.algorithm, signed.algorithm_version,
            signed.key_version,
            str(document.get("layer_control_mode") or document.get("resolution") or "manual_quality"),
            "active", expires, expires + 300_000,
        )
        result = self._repository.save(projection)
        if result.status in {"saved", "replayed"} and result.projection is not None:
            return result.projection
        raise SfuLayerProjectionError(result.reason_code, 409)

    def read(self, *, scope: SfuProjectionScope, projection_kind: ProjectionKind,
             subject_ref: str, cursor: int = 0) -> SfuStoredLayerProjection | None:
        now_ms = int(self._clock() * 1000)
        self._enforce_read_rate(scope, projection_kind, now_ms)
        projection = self._repository.current(
            tenant_id=scope.tenant_id, room_id=scope.room_id,
            projection_kind=projection_kind, subject_ref=subject_ref,
        )
        if projection is None or projection.status != "active" or projection.expires_at_ms <= now_ms:
            return None
        if projection.membership_epoch != scope.membership_epoch:
            return None
        if projection_kind != "room" and scope.route_epoch and projection.route_epoch != scope.route_epoch:
            return None
        if projection_kind == "receiver" and (
            (scope.topology_epoch and projection.topology_epoch != scope.topology_epoch)
            or (scope.key_epoch and projection.key_epoch != scope.key_epoch)
        ):
            return None
        return None if projection.projection_version <= cursor else projection

    def record_receipt(self, *, scope: SfuProjectionScope, projection_ref: str,
                       actor_digest: str, raw_document: bytes) -> bool:
        if len(raw_document) > 8192:
            raise SfuLayerProjectionError("sfu_projection_receipt_bytes_exceeded", 413)
        try:
            document = json.loads(raw_document.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SfuLayerProjectionError("sfu_projection_receipt_json_invalid") from exc
        if not isinstance(document, dict) or document.get("schema") != "ananta.sfu-projection-application-receipt.v1":
            raise SfuLayerProjectionError("sfu_projection_receipt_schema_invalid")
        if document.get("tenant_ref") != scope.tenant_id or document.get("room_ref") != scope.room_id:
            raise SfuLayerProjectionError("sfu_projection_receipt_cross_scope", 403)
        if document.get("projection_ref") != projection_ref:
            raise SfuLayerProjectionError("sfu_projection_receipt_projection_mismatch", 409)
        sequence = _positive(document.get("receipt_sequence"), "receipt_sequence")
        expires = _utc_ms(document.get("expires_at"))
        now_ms = int(self._clock() * 1000)
        if expires <= now_ms or expires > now_ms + 15_000:
            raise SfuLayerProjectionError("sfu_projection_receipt_stale", 409)
        encoded = _canonical(document)
        digest = hashlib.sha256(encoded).hexdigest()
        receipt = SfuStoredProjectionReceipt(
            "sfu-prc-" + hashlib.sha256((projection_ref + actor_digest + str(sequence)).encode()).hexdigest()[:32],
            scope.tenant_id, projection_ref, actor_digest, sequence, document, digest, expires,
        )
        if not self._repository.save_receipt(receipt, history_max=8):
            raise SfuLayerProjectionError("sfu_projection_receipt_conflict", 409)
        return True

    def _enforce_read_rate(self, scope: SfuProjectionScope, kind: ProjectionKind, now_ms: int) -> None:
        key = (scope.tenant_id, scope.room_id, f"{scope.actor_id}:{kind}")
        with self._rate_lock:
            recent = [stamp for stamp in self._reads.get(key, ()) if stamp > now_ms - 60_000]
            limit = 4 if kind == "room" else 12
            if len(recent) >= limit:
                raise SfuLayerProjectionError("sfu_projection_rate_limited", 429, 5)
            recent.append(now_ms)
            self._reads[key] = recent


def _validate_projection(command: SfuProjectionMaterializeCommand, document: Mapping[str, object], now_ms: int) -> None:
    if document.get("schema") != _SCHEMAS[command.projection_kind] or document.get("schema_version") != 1:
        raise SfuLayerProjectionError("sfu_projection_schema_invalid")
    if document.get("tenant_ref") != command.tenant_id or document.get("room_ref") != command.room_id:
        raise SfuLayerProjectionError("sfu_projection_cross_scope", 403)
    expected_subject = {
        "room": document.get("room_ref"),
        "publisher": document.get("publication_ref"),
        "receiver": document.get("subscription_ref"),
    }[command.projection_kind]
    if expected_subject != command.subject_ref:
        raise SfuLayerProjectionError("sfu_projection_subject_mismatch", 409)
    issued = _utc_ms(document.get("issued_at"))
    expires = _utc_ms(document.get("expires_at"))
    if issued > now_ms + 5_000 or expires <= now_ms or expires - issued > _TTL_MAX[command.projection_kind]:
        raise SfuLayerProjectionError("sfu_projection_ttl_invalid", 409)


def _projection_id(command: SfuProjectionMaterializeCommand) -> str:
    return "sfu-prj-" + hashlib.sha256(
        f"{command.tenant_id}\0{command.room_id}\0{command.projection_kind}\0{command.subject_ref}".encode()
    ).hexdigest()[:32]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _positive(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise SfuLayerProjectionError(f"sfu_projection_{name}_invalid")
    return value


def _nonnegative(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise SfuLayerProjectionError(f"sfu_projection_{name}_invalid")
    return value


def _optional_epoch(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    return 0 if value is None else _positive(value, key)


def _utc_ms(value: object) -> int:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z"):
        raise SfuLayerProjectionError("sfu_projection_timestamp_invalid")
    try:
        return int(datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc).timestamp() * 1000)
    except ValueError as exc:
        raise SfuLayerProjectionError("sfu_projection_timestamp_invalid") from exc


__all__ = [
    "HmacSfuProjectionSigner", "SfuLayerProjectionError", "SfuLayerProjectionService",
    "SfuProjectionMaterializeCommand", "SfuProjectionScope", "SfuProjectionScopeAuthorizerPort",
    "SfuProjectionSignerPort",
]
