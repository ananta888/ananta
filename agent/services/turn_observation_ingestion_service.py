"""Authenticated, signed and bounded TURN pool observation ingestion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator

from agent.repositories.turn_observation_cursor_repository import (
    SqlTurnObservationCursorRepository,
    TurnObservationRepositoryError,
)
from agent.services.turn_observer_identity_service import (
    TurnObserverIdentityError,
    TurnObserverIdentityService,
    TurnObserverTransportIdentity,
)
from agent.services.turn_pool_contract import TURN_POOL_CONTRACT_VERSION


class TurnObservationError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class TurnPoolObservationProjectionPort(Protocol):
    def apply_observation(self, *, cursor, normalized: dict) -> None: ...


@dataclass(frozen=True, slots=True)
class TurnObservationIngestResult:
    status: str
    reason_codes: tuple[str, ...]
    sequence: int
    fencing_token: int
    cursor_version: int
    health_status: str
    capacity_status: str

    def public(self) -> dict[str, object]:
        return {
            "ok": True,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "sequence": self.sequence,
            "fencing_token": self.fencing_token,
            "cursor_version": self.cursor_version,
            "health_status": self.health_status,
            "capacity_status": self.capacity_status,
            "authoritative": False,
        }


class TurnObservationIngestionService:
    def __init__(
        self,
        repository: SqlTurnObservationCursorRepository,
        *,
        identities: TurnObserverIdentityService,
        schema_path: Path,
        digest_secret: bytes,
        directory: TurnPoolObservationProjectionPort | None = None,
        clock: Callable[[], float] = time.time,
        max_report_bytes: int = 32_768,
        max_clock_skew_seconds: int = 5,
        max_window_seconds: int = 300,
        max_reports_per_minute: int = 120,
        replay_entries_max: int = 2048,
        replay_ttl_seconds: int = 3600,
        retention_seconds: int = 86_400,
    ) -> None:
        if len(digest_secret) < 32:
            raise TurnObservationError("turn_observation_digest_secret_invalid", 503)
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TurnObservationError("turn_observation_schema_unavailable", 503) from exc
        self._validator = Draft202012Validator(schema)
        self._repository = repository
        self._identities = identities
        self._directory = directory
        self._secret = bytes(digest_secret)
        self._clock = clock
        self._max_bytes = max_report_bytes
        self._max_skew = max_clock_skew_seconds
        self._max_window = max_window_seconds
        self._max_rate = max_reports_per_minute
        self._replay_max = replay_entries_max
        self._replay_ttl = replay_ttl_seconds
        self._retention = retention_seconds

    def ingest(self, raw_document: bytes, transport: TurnObserverTransportIdentity) -> TurnObservationIngestResult:
        if not isinstance(raw_document, bytes) or not raw_document or len(raw_document) > self._max_bytes:
            raise TurnObservationError("turn_observation_report_bytes_invalid", 413)
        try:
            document = json.loads(raw_document)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TurnObservationError("turn_observation_document_invalid") from exc
        errors = tuple(self._validator.iter_errors(document))
        if errors:
            raise TurnObservationError("turn_observation_schema_invalid")
        try:
            authorization = self._identities.authorize(transport)
        except TurnObserverIdentityError as exc:
            raise TurnObservationError(exc.reason_code, exc.status_code) from exc
        if (
            document["pool_id"] != authorization.pool_id
            or document["instance_id"] != authorization.instance_id
            or document["observer_identity_id"] != authorization.identity_id
            or document["identity_version"] != authorization.identity_version
            or document["region"] != authorization.region
        ):
            raise TurnObservationError("turn_observation_scope_impersonation", 403)
        canonical = dict(document)
        signature_value = canonical.pop("signature")
        canonical_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(authorization.public_key_b64, validate=True)
            )
            public_key.verify(base64.b64decode(signature_value, validate=True), b"ananta.turn-observation.v1\0" + canonical_bytes)
        except (ValueError, InvalidSignature) as exc:
            raise TurnObservationError("turn_observation_signature_invalid", 403) from exc
        now = float(self._clock())
        measured = float(document["measured_at_seconds"])
        window = int(document["window_seconds"])
        if not math.isfinite(measured) or abs(now - measured) > self._max_skew:
            raise TurnObservationError("turn_observation_clock_skew")
        if window < 1 or window > self._max_window or document["window_started_at_seconds"] + window > measured + 1:
            raise TurnObservationError("turn_observation_window_invalid")
        if self._repository.count_recent(
            pool_id=document["pool_id"], instance_id=document["instance_id"], since=now - 60
        ) >= self._max_rate:
            raise TurnObservationError("turn_observation_rate_exceeded", 429)
        counters = document["counters"]
        known = all(value is not None for value in counters.values())
        implausible = any(isinstance(value, int) and value > 2**63 - 1 for value in counters.values())
        health = document["health_status"]
        if implausible or counters.get("exhaustion_events", 0):
            capacity = "stop"
        elif not known:
            capacity = "unknown"
        elif health == "healthy":
            capacity = "ready"
        else:
            capacity = "stop"
        normalized = {
            "contract_version": TURN_POOL_CONTRACT_VERSION,
            "node_id": document["pool_id"],
            "pool_id": document["pool_id"],
            "instance_id": document["instance_id"],
            "region": document["region"],
            "credential_binding_mode": document["credential_binding_mode"],
            "config_digest": document["config_digest"],
            "measured_at_seconds": measured,
            "observed_at": measured,
            "health_status": health,
            "capacity_status": {"ready": "accept", "unknown": "stop"}.get(capacity, capacity),
            "relay_status": "ready" if health == "healthy" and capacity == "ready" else "not_ready",
            "fresh_until": measured + min(window * 2, self._max_window),
            "counters": counters,
        }
        payload_digest = hashlib.sha256(canonical_bytes).hexdigest()
        try:
            status, cursor, reasons = self._repository.ingest(
                document=document,
                normalized=normalized,
                payload_digest=payload_digest,
                observation_id_digest=self._digest("observation", document["observation_id"]),
                boot_id_digest=self._digest("boot", document["boot_id"]),
                observer_identity_id=authorization.identity_id,
                observer_identity_version=authorization.identity_version,
                now=now,
                replay_ttl_seconds=self._replay_ttl,
                retention_seconds=self._retention,
                replay_entries_max=self._replay_max,
                retired_boot_ids_max=16,
            )
        except TurnObservationRepositoryError as exc:
            status_code = 409 if any(value in exc.reason_code for value in ("replay", "conflict", "stale")) else 503
            raise TurnObservationError(exc.reason_code, status_code) from exc
        normalized = dict(cursor.normalized_observation_json)
        if self._directory is not None:
            try:
                self._directory.apply_observation(cursor=cursor, normalized=normalized)
            except Exception as exc:  # noqa: BLE001 - directory failure can only stop readiness.
                raise TurnObservationError("turn_observation_directory_unavailable", 503) from exc
        return TurnObservationIngestResult(
            status,
            reasons,
            cursor.highest_sequence,
            cursor.fencing_token,
            cursor.version,
            cursor.health_status,
            cursor.capacity_status,
        )

    def _digest(self, domain: str, value: str) -> str:
        return hmac.new(self._secret, f"turn-observation-{domain}-v1\0{value}".encode(), hashlib.sha256).hexdigest()


__all__ = [
    "TurnObservationError",
    "TurnObservationIngestResult",
    "TurnObservationIngestionService",
    "TurnPoolObservationProjectionPort",
]
