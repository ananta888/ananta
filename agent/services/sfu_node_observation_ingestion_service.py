"""Mode-authenticated, bounded SFU health/capacity observation ingestion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Protocol

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, padding, rsa

from agent.repositories.sfu_node_observation_cursor_repository import (
    SfuNodeObservationAcceptance,
    SfuNodeObservationCursorError,
    SfuNodeObservationCursorRepositoryPort,
)
from agent.repositories.sfu_node_repository import (
    SfuNodeRecord,
    SfuNodeRepositoryError,
    SfuNodeRepositoryPort,
)
from agent.services.sfu_broadcast_contract_validator import (
    ContractDefinition,
    SfuBroadcastContractValidator,
    StructuralLimits,
    ValidationContext,
)
from agent.services.sfu_node_identity_service import (
    AUTHENTICATED_RUNTIME_EXTENSION,
    LIVEKIT_CONTROL_API,
    SFU_OBSERVER,
    SfuNodeIdentityError,
)


CONTRACT_ID = "ananta.sfu-node-health-capacity.v1"
SIGNATURE_DOMAIN = b"ananta.sfu-node-health-capacity.v1"
INTEGER_METRICS = frozenset(
    {
        "memory_bytes",
        "fd_count",
        "ingress_bps",
        "egress_bps",
        "rooms",
        "tracks",
        "receivers",
    }
)


class SfuNodeObservationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuNodeObservationAuthentication:
    transport_tls_verified: bool
    collector_authenticated: bool = False
    peer_certificate_pem: str | None = None


@dataclass(frozen=True, slots=True)
class SfuNodeObservationPolicy:
    runtime_control_mode: str
    collector_id: str = ""
    sequence_window: int = 64
    cursor_entries_max: int = 256
    cursor_ttl_seconds: int = 3600
    replay_retention_seconds: int = 86_400
    observation_ttl_max_seconds: int = 30
    clock_skew_seconds: int = 5
    boot_sequence_start_max: int = 1
    hard_limits: Mapping[str, float] = field(
        default_factory=lambda: {
            "cpu_percent": 95.0,
            "memory_bytes": 137_438_953_472,
            "fd_count": 1_000_000,
            "ingress_bps": 100_000_000_000,
            "egress_bps": 100_000_000_000,
            "turn_ratio": 1.0,
            "rooms": 100_000,
            "tracks": 1_000_000,
            "receivers": 10_000_000,
        }
    )

    def __post_init__(self) -> None:
        if self.runtime_control_mode not in {
            AUTHENTICATED_RUNTIME_EXTENSION,
            LIVEKIT_CONTROL_API,
        }:
            raise ValueError("sfu_observation_runtime_mode_invalid")
        for value in (
            self.sequence_window,
            self.cursor_entries_max,
            self.cursor_ttl_seconds,
            self.replay_retention_seconds,
            self.observation_ttl_max_seconds,
            self.clock_skew_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("sfu_observation_policy_bound_invalid")
        if self.cursor_entries_max < self.sequence_window or self.cursor_entries_max > 10_000:
            raise ValueError("sfu_observation_cursor_bounds_invalid")
        if set(self.hard_limits) != set(_METRIC_SCHEMAS):
            raise ValueError("sfu_observation_hard_limits_incomplete")
        if any(float(value) < 0 for value in self.hard_limits.values()):
            raise ValueError("sfu_observation_hard_limit_invalid")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
        *,
        runtime_control_mode: str,
    ) -> "SfuNodeObservationPolicy":
        defaults = cls(runtime_control_mode=runtime_control_mode)
        hard_limits = {
            name: _environment_number(
                environ,
                f"ANANTA_SFU_OBSERVATION_{name.upper()}_MAX",
                default=float(defaults.hard_limits[name]),
            )
            for name in _METRIC_SCHEMAS
        }
        return cls(
            runtime_control_mode=runtime_control_mode,
            collector_id=str(environ.get("ANANTA_SFU_OBSERVATION_COLLECTOR_ID") or "").strip(),
            sequence_window=_environment_integer(
                environ,
                "ANANTA_SFU_OBSERVATION_SEQUENCE_WINDOW",
                defaults.sequence_window,
            ),
            cursor_entries_max=_environment_integer(
                environ,
                "ANANTA_SFU_OBSERVATION_CURSOR_ENTRIES_MAX",
                defaults.cursor_entries_max,
            ),
            cursor_ttl_seconds=_environment_integer(
                environ,
                "ANANTA_SFU_OBSERVATION_CURSOR_TTL_SECONDS",
                defaults.cursor_ttl_seconds,
            ),
            replay_retention_seconds=_environment_integer(
                environ,
                "ANANTA_SFU_OBSERVATION_RETENTION_SECONDS",
                defaults.replay_retention_seconds,
            ),
            observation_ttl_max_seconds=_environment_integer(
                environ,
                "ANANTA_SFU_OBSERVATION_TTL_MAX_SECONDS",
                defaults.observation_ttl_max_seconds,
            ),
            clock_skew_seconds=_environment_integer(
                environ,
                "ANANTA_SFU_OBSERVATION_CLOCK_SKEW_SECONDS",
                defaults.clock_skew_seconds,
            ),
            boot_sequence_start_max=defaults.boot_sequence_start_max,
            hard_limits=hard_limits,
        )


@dataclass(frozen=True, slots=True)
class SfuNodeObservationResult:
    status: str
    observation_id: str
    normalized_observation: Mapping[str, object]
    node: SfuNodeRecord | None

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "observation_id": self.observation_id,
            "normalized_observation": dict(self.normalized_observation),
            "node": None if self.node is None else self.node.payload(),
        }


class SfuNodeIdentityAuthorizationPort(Protocol):
    def authorize_extension_peer(
        self,
        *,
        node_id: str,
        certificate_pem: str,
        required_role: str,
        transport_peer_verified: bool,
    ): ...


class SfuNodeObservationIngestionService:
    """Validates and persists observations without invoking Admission policy."""

    def __init__(
        self,
        *,
        cursor_repository: SfuNodeObservationCursorRepositoryPort,
        node_repository: SfuNodeRepositoryPort,
        identity_service: SfuNodeIdentityAuthorizationPort,
        policy: SfuNodeObservationPolicy,
        validator: SfuBroadcastContractValidator,
        clock=time.time,
    ) -> None:
        self._cursors = cursor_repository
        self._nodes = node_repository
        self._identities = identity_service
        self._policy = policy
        self._validator = validator
        self._clock = clock

    def ingest(
        self,
        raw_document: bytes | str,
        authentication: SfuNodeObservationAuthentication,
    ) -> SfuNodeObservationResult:
        validation = self._validator.validate(CONTRACT_ID, raw_document, ValidationContext())
        if not validation.valid:
            status_code = 413 if "exceeded" in validation.reason_code else 400
            raise SfuNodeObservationError(validation.reason_code, status_code=status_code)
        document = _decode_validated_document(raw_document)
        producer_mode = str(document["producer_mode"])
        if producer_mode != self._policy.runtime_control_mode:
            raise SfuNodeObservationError(
                "sfu_observation_producer_mode_mismatch",
                status_code=403,
            )
        self._authenticate(document, authentication)

        now = float(self._clock())
        measured_at = float(document["measured_at"])
        if measured_at > now + self._policy.clock_skew_seconds:
            raise SfuNodeObservationError("sfu_observation_clock_skew")
        ttl_seconds = min(
            int(document["ttl_seconds"]),
            self._policy.observation_ttl_max_seconds,
        )
        fresh_until = measured_at + ttl_seconds
        normalized = _normalize_observation(document, self._policy.hard_limits, ttl_seconds)
        payload_digest = hashlib.sha256(_canonical_unsigned_document(document)).hexdigest()
        node_id_raw = document.get("node_id")
        node_id = str(node_id_raw) if node_id_raw is not None else None
        fencing_token = int(document["fencing_token"])

        try:
            acceptance = self._cursors.accept_observation(
                tenant_id=str(document["tenant_id"]),
                cluster_id=str(document["cluster_id"]),
                region=str(document["region"]),
                node_id=node_id,
                producer_mode=producer_mode,
                producer_id=str(document["producer_id"]),
                boot_id=str(document["boot_id"]),
                sequence=int(document["sequence"]),
                measured_at=measured_at,
                fresh_until=fresh_until,
                payload_digest=payload_digest,
                normalized_observation=normalized,
                fencing_token=fencing_token,
                sequence_window=self._policy.sequence_window,
                entries_max=self._policy.cursor_entries_max,
                cursor_ttl_seconds=self._policy.cursor_ttl_seconds,
                retention_seconds=self._policy.replay_retention_seconds,
                boot_sequence_start_max=self._policy.boot_sequence_start_max,
            )
        except SfuNodeObservationCursorError as exc:
            raise _cursor_error(exc) from exc

        if acceptance.status == "duplicate" and acceptance.applied_node_version is not None:
            current = self._load_node(document, node_id)
            return SfuNodeObservationResult(
                status="duplicate",
                observation_id=acceptance.observation_id,
                normalized_observation=acceptance.normalized_observation,
                node=current,
            )

        node = self._load_and_fence_node(document, node_id, fencing_token)
        if node is None or acceptance.status == "accepted_reordered":
            return SfuNodeObservationResult(
                status=acceptance.status,
                observation_id=acceptance.observation_id,
                normalized_observation=acceptance.normalized_observation,
                node=node,
            )
        observation_id = f"sfu-observation:{acceptance.observation_id}"
        current = self._nodes.get_node(
            tenant_id=node.tenant_id,
            cluster_id=node.cluster_id,
            node_id=node.node_id,
        )
        if current is not None and current.last_observation_id == observation_id:
            updated = current
        else:
            try:
                updated = self._nodes.record_observation(
                    tenant_id=node.tenant_id,
                    cluster_id=node.cluster_id,
                    node_id=node.node_id,
                    observation_id=observation_id,
                    region=str(document["region"]),
                    adapter_name=str(document["adapter_name"]),
                    adapter_version=str(document["adapter_version"]),
                    protocol_version=str(document["protocol_version"]),
                    capability_digest=str(document["capability_digest"]),
                    health_status=str(document["health_status"]),
                    observation_ttl_seconds=ttl_seconds,
                    expected_version=int(document["node_version"]),
                    fencing_token=fencing_token,
                )
            except SfuNodeRepositoryError as exc:
                raise _node_error(exc) from exc
        try:
            self._cursors.mark_applied(
                receipt_id=acceptance.receipt_id,
                node_version=updated.version,
            )
        except SfuNodeObservationCursorError as exc:
            raise _cursor_error(exc) from exc
        return SfuNodeObservationResult(
            status=acceptance.status,
            observation_id=acceptance.observation_id,
            normalized_observation=acceptance.normalized_observation,
            node=updated,
        )

    def _authenticate(
        self,
        document: Mapping[str, object],
        authentication: SfuNodeObservationAuthentication,
    ) -> None:
        if not authentication.transport_tls_verified:
            raise SfuNodeObservationError("sfu_observation_tls_required", status_code=403)
        mode = str(document["producer_mode"])
        signature = document.get("signature")
        if mode == LIVEKIT_CONTROL_API:
            if not authentication.collector_authenticated or not self._policy.collector_id:
                raise SfuNodeObservationError(
                    "sfu_observation_collector_unauthorized",
                    status_code=403,
                )
            if document["producer_id"] != self._policy.collector_id:
                raise SfuNodeObservationError(
                    "sfu_observation_collector_identity_mismatch",
                    status_code=403,
                )
            if signature is not None:
                raise SfuNodeObservationError(
                    "sfu_observation_unexpected_node_signature",
                    status_code=403,
                )
            return

        node_id = document.get("node_id")
        certificate_pem = authentication.peer_certificate_pem
        if not isinstance(node_id, str) or not node_id:
            raise SfuNodeObservationError(
                "sfu_observation_extension_node_required",
                status_code=403,
            )
        if not isinstance(certificate_pem, str) or not certificate_pem:
            raise SfuNodeObservationError(
                "sfu_observation_mtls_identity_required",
                status_code=403,
            )
        try:
            identity = self._identities.authorize_extension_peer(
                node_id=node_id,
                certificate_pem=certificate_pem,
                required_role=SFU_OBSERVER,
                transport_peer_verified=True,
            )
        except SfuNodeIdentityError as exc:
            raise SfuNodeObservationError(exc.reason_code, status_code=403) from exc
        if document["producer_id"] != getattr(identity, "id", None):
            raise SfuNodeObservationError(
                "sfu_observation_runtime_identity_mismatch",
                status_code=403,
            )
        _verify_payload_signature(document, certificate_pem, signature)

    def _load_and_fence_node(
        self,
        document: Mapping[str, object],
        node_id: str | None,
        fencing_token: int,
    ) -> SfuNodeRecord | None:
        if node_id is None:
            if document["producer_mode"] == AUTHENTICATED_RUNTIME_EXTENSION:
                raise SfuNodeObservationError("sfu_observation_extension_node_required")
            if fencing_token != 0 or int(document["node_version"]) != 0:
                raise SfuNodeObservationError("sfu_observation_cluster_fence_invalid")
            return None
        node = self._load_node(document, node_id)
        if node is None:
            raise SfuNodeObservationError("sfu_observation_node_not_found", status_code=404)
        if node.revoked:
            raise SfuNodeObservationError("sfu_observation_node_revoked", status_code=403)
        if node.region != document["region"]:
            raise SfuNodeObservationError("sfu_observation_region_mismatch", status_code=403)
        if fencing_token != node.fencing_token:
            raise SfuNodeObservationError("sfu_observation_fencing_mismatch", status_code=409)
        if int(document["node_version"]) != node.version:
            raise SfuNodeObservationError("sfu_observation_node_version_conflict", status_code=409)
        return node

    def _load_node(
        self,
        document: Mapping[str, object],
        node_id: str | None,
    ) -> SfuNodeRecord | None:
        if node_id is None:
            return None
        try:
            return self._nodes.get_node(
                tenant_id=str(document["tenant_id"]),
                cluster_id=str(document["cluster_id"]),
                node_id=node_id,
            )
        except SfuNodeRepositoryError as exc:
            raise _node_error(exc) from exc


def build_sfu_node_observation_validator(*, clock=time.time) -> SfuBroadcastContractValidator:
    return SfuBroadcastContractValidator(
        definitions=(
            ContractDefinition(
                contract_id=CONTRACT_ID,
                schema_version="1",
                schema=_OBSERVATION_SCHEMA,
                signature_required=False,
            ),
        ),
        clock=_ValidatorClock(clock),
        trust_store=_NoSignatureTrustStore(),
        limits=StructuralLimits(
            max_document_bytes=32_768,
            max_depth=8,
            max_nodes=512,
            max_collection_items=64,
            max_string_bytes=1024,
            max_total_string_bytes=16_384,
        ),
    )


def build_sfu_node_observation_signature_message(document: Mapping[str, object]) -> bytes:
    return SIGNATURE_DOMAIN + b"\x00" + _canonical_unsigned_document(document)


def collector_token_digest(token: str) -> str:
    return hashlib.sha256(
        b"ananta.sfu-observation.collector-token.v1\x00" + token.encode("utf-8")
    ).hexdigest()


def authenticate_collector_token(token: str, expected_digest: str | None) -> bool:
    if not token or not expected_digest:
        return False
    return hmac.compare_digest(collector_token_digest(token), expected_digest)


def _verify_payload_signature(
    document: Mapping[str, object],
    certificate_pem: str,
    signature_value: object,
) -> None:
    if not isinstance(signature_value, Mapping):
        raise SfuNodeObservationError(
            "sfu_observation_payload_signature_required",
            status_code=403,
        )
    algorithm = signature_value.get("algorithm")
    encoded = signature_value.get("value")
    if not isinstance(algorithm, str) or not isinstance(encoded, str):
        raise SfuNodeObservationError(
            "sfu_observation_payload_signature_invalid",
            status_code=403,
        )
    try:
        signature = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
        public_key = certificate.public_key()
        message = build_sfu_node_observation_signature_message(document)
        if isinstance(public_key, ed25519.Ed25519PublicKey):
            if algorithm != "Ed25519":
                raise ValueError("algorithm")
            public_key.verify(signature, message)
        elif isinstance(public_key, ed448.Ed448PublicKey):
            if algorithm != "Ed448":
                raise ValueError("algorithm")
            public_key.verify(signature, message)
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            if algorithm != "ECDSA-SHA256":
                raise ValueError("algorithm")
            public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        elif isinstance(public_key, rsa.RSAPublicKey):
            if algorithm != "RSA-PSS-SHA256":
                raise ValueError("algorithm")
            public_key.verify(
                signature,
                message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
        else:
            raise ValueError("key type")
    except (InvalidSignature, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SfuNodeObservationError(
            "sfu_observation_payload_signature_invalid",
            status_code=403,
        ) from exc


def _normalize_observation(
    document: Mapping[str, object],
    hard_limits: Mapping[str, float],
    ttl_seconds: int,
) -> dict[str, object]:
    reported = document["metrics"]
    assert isinstance(reported, Mapping)
    metrics: dict[str, int | float] = {}
    clamped_fields: list[str] = []
    for name in _METRIC_SCHEMAS:
        raw = reported[name]
        limit = hard_limits[name]
        value = min(float(raw), float(limit))
        if float(raw) > float(limit):
            clamped_fields.append(name)
        metrics[name] = int(value) if name in INTEGER_METRICS else value
    return {
        "schema_version": 1,
        "producer_mode": document["producer_mode"],
        "tenant_id": document["tenant_id"],
        "cluster_id": document["cluster_id"],
        "region": document["region"],
        "node_id": document.get("node_id"),
        "producer_id": document["producer_id"],
        "boot_id": document["boot_id"],
        "sequence": document["sequence"],
        "measured_at": document["measured_at"],
        "ttl_seconds": ttl_seconds,
        "adapter_name": document["adapter_name"],
        "adapter_version": document["adapter_version"],
        "protocol_version": document["protocol_version"],
        "capability_digest": document["capability_digest"],
        "health_status": document["health_status"],
        "reported_drain_state": document["drain_state"],
        "metrics": metrics,
        "labels": dict(document.get("labels") or {}),
        "validity": "clamped" if clamped_fields else "valid",
        "clamped_fields": sorted(clamped_fields),
        "authoritative": False,
    }


def _decode_validated_document(raw_document: bytes | str) -> dict[str, object]:
    raw = raw_document.decode("utf-8") if isinstance(raw_document, bytes) else raw_document
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def _canonical_unsigned_document(document: Mapping[str, object]) -> bytes:
    unsigned = {key: value for key, value in document.items() if key != "signature"}
    return json.dumps(
        unsigned,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _cursor_error(exc: SfuNodeObservationCursorError) -> SfuNodeObservationError:
    status = 503 if "unavailable" in exc.reason_code else 409
    if "stale" in exc.reason_code or "outside_window" in exc.reason_code:
        status = 422
    return SfuNodeObservationError(exc.reason_code, status_code=status)


def _node_error(exc: SfuNodeRepositoryError) -> SfuNodeObservationError:
    status = 503 if "unavailable" in exc.reason_code else 409
    if "not_found" in exc.reason_code:
        status = 404
    if "revoked" in exc.reason_code:
        status = 403
    return SfuNodeObservationError(exc.reason_code, status_code=status)


def _environment_integer(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name.lower()}_invalid") from exc


def _environment_number(
    environ: Mapping[str, str], name: str, *, default: float
) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name.lower()}_invalid") from exc


class _ValidatorClock:
    def __init__(self, clock) -> None:
        self._clock = clock

    def now(self) -> datetime:
        return datetime.fromtimestamp(float(self._clock()), timezone.utc)


class _NoSignatureTrustStore:
    def verify(self, contract_id: str, document: Mapping[str, object]) -> bool:
        return False


_METRIC_SCHEMAS: dict[str, dict[str, object]] = {
    "cpu_percent": {"type": "number", "minimum": 0, "maximum": 1_000_000},
    "memory_bytes": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
    "fd_count": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
    "ingress_bps": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
    "egress_bps": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
    "turn_ratio": {"type": "number", "minimum": 0, "maximum": 1_000_000},
    "rooms": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
    "tracks": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
    "receivers": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
}

_OBSERVATION_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ananta.invalid/contracts/sfu-node-health-capacity-v1.schema.json",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "producer_mode",
        "tenant_id",
        "cluster_id",
        "region",
        "node_id",
        "producer_id",
        "boot_id",
        "sequence",
        "measured_at",
        "ttl_seconds",
        "adapter_name",
        "adapter_version",
        "protocol_version",
        "capability_digest",
        "health_status",
        "drain_state",
        "fencing_token",
        "node_version",
        "metrics",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "producer_mode": {
            "enum": [AUTHENTICATED_RUNTIME_EXTENSION, LIVEKIT_CONTROL_API]
        },
        "tenant_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "cluster_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "region": {"type": "string", "minLength": 1, "maxLength": 128},
        "node_id": {
            "oneOf": [
                {"type": "string", "minLength": 1, "maxLength": 200},
                {"type": "null"},
            ]
        },
        "producer_id": {"type": "string", "minLength": 1, "maxLength": 200},
        "boot_id": {"type": "string", "minLength": 8, "maxLength": 200},
        "sequence": {"type": "integer", "minimum": 0, "maximum": 9_007_199_254_740_991},
        "measured_at": {"type": "number", "minimum": 0},
        "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
        "adapter_name": {"type": "string", "minLength": 1, "maxLength": 128},
        "adapter_version": {"type": "string", "minLength": 1, "maxLength": 128},
        "protocol_version": {"type": "string", "minLength": 1, "maxLength": 128},
        "capability_digest": {
            "type": "string",
            "pattern": "^sha256:[0-9a-f]{64}$",
        },
        "health_status": {"enum": ["healthy", "degraded", "unhealthy"]},
        "drain_state": {"enum": ["active", "draining", "drained"]},
        "fencing_token": {"type": "integer", "minimum": 0},
        "node_version": {"type": "integer", "minimum": 0},
        "metrics": {
            "type": "object",
            "additionalProperties": False,
            "required": list(_METRIC_SCHEMAS),
            "properties": _METRIC_SCHEMAS,
        },
        "labels": {
            "type": "object",
            "maxProperties": 16,
            "propertyNames": {"pattern": "^[a-z][a-z0-9_.-]{0,63}$"},
            "additionalProperties": {"type": "string", "maxLength": 128},
        },
        "signature": {
            "type": "object",
            "additionalProperties": False,
            "required": ["algorithm", "value"],
            "properties": {
                "algorithm": {
                    "enum": ["Ed25519", "Ed448", "ECDSA-SHA256", "RSA-PSS-SHA256"]
                },
                "value": {"type": "string", "minLength": 16, "maxLength": 2048},
            },
        },
    },
}


__all__ = [
    "CONTRACT_ID",
    "SfuNodeObservationAuthentication",
    "SfuNodeObservationError",
    "SfuNodeObservationIngestionService",
    "SfuNodeObservationPolicy",
    "SfuNodeObservationResult",
    "authenticate_collector_token",
    "build_sfu_node_observation_signature_message",
    "build_sfu_node_observation_validator",
    "collector_token_digest",
]
