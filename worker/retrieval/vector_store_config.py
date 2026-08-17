from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from worker.retrieval.vector_store_endpoint_policy import (
    SecretReference,
    VectorStoreEndpointPolicyError,
    normalize_allowed_origins,
    normalize_trusted_private_origins,
    validate_endpoint_access,
)


class VectorStoreConfigError(ValueError):
    def __init__(self, reason: str, *, cause_reason: str | None = None) -> None:
        self.reason = str(reason or "invalid_vector_store_config")
        self.cause_reason = str(cause_reason or "").strip() or None
        message = (
            f"{self.reason}:{self.cause_reason}"
            if self.cause_reason and self.cause_reason != self.reason
            else self.reason
        )
        super().__init__(message)


class VectorStoreProvider(str, Enum):
    JSON = "json"
    QDRANT = "qdrant"
    DUCKDB = "duckdb"


class VectorStoreDistance(str, Enum):
    COSINE = "cosine"
    DOT = "dot"
    EUCLID = "euclid"


class AvailabilityMode(str, Enum):
    FAIL_FAST = "fail_fast"
    DEGRADED_EMPTY = "degraded_empty"
    EXPLICIT_JSON_FALLBACK = "explicit_json_fallback"


def _enum_value(
    enum_type: type[Enum],
    value: object,
    reason: str,
    *,
    cause_reason: str | None = None,
) -> Any:
    try:
        return enum_type(str(value).strip().lower())
    except ValueError as exc:
        raise VectorStoreConfigError(reason, cause_reason=cause_reason) from exc


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], reason: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise VectorStoreConfigError(f"{reason}:{','.join(unknown)}")


def _strict_bool(value: Any, *, cause_reason: str) -> bool:
    if type(value) is not bool:
        raise VectorStoreConfigError(
            "vector_store_invalid_boolean",
            cause_reason=cause_reason,
        )
    return value


def _strict_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VectorStoreConfigError(
            "vector_store_invalid_timeout",
            cause_reason="invalid_qdrant_timeout_type",
        )
    return float(value)


def _origin_sequence(
    value: Any,
    *,
    allow_empty: bool,
    cause_reason: str,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise VectorStoreConfigError(
            "vector_store_invalid_origin",
            cause_reason=cause_reason,
        )
    if not value and not allow_empty:
        raise VectorStoreConfigError(
            "vector_store_invalid_origin",
            cause_reason="vector_store_allowed_origins_required",
        )
    origins: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise VectorStoreConfigError(
                "vector_store_invalid_origin",
                cause_reason=cause_reason,
            )
        origins.append(item)
    return tuple(origins)


@dataclass(frozen=True, slots=True)
class AvailabilityPolicy:
    on_unavailable: AvailabilityMode = AvailabilityMode.DEGRADED_EMPTY
    fallback_provider: VectorStoreProvider | None = None

    def __post_init__(self) -> None:
        mode = (
            self.on_unavailable
            if isinstance(self.on_unavailable, AvailabilityMode)
            else _enum_value(
                AvailabilityMode,
                self.on_unavailable,
                "vector_store_invalid_availability_policy",
                cause_reason="invalid_vector_store_availability_mode",
            )
        )
        fallback = self.fallback_provider
        if fallback is not None and not isinstance(fallback, VectorStoreProvider):
            fallback = _enum_value(
                VectorStoreProvider,
                fallback,
                "vector_store_invalid_availability_policy",
                cause_reason="unknown_vector_store_fallback_provider",
            )
        if mode == AvailabilityMode.EXPLICIT_JSON_FALLBACK and fallback not in {None, VectorStoreProvider.JSON}:
            raise VectorStoreConfigError(
                "vector_store_invalid_availability_policy",
                cause_reason="explicit_fallback_requires_json_provider",
            )
        if mode == AvailabilityMode.EXPLICIT_JSON_FALLBACK:
            fallback = VectorStoreProvider.JSON
        object.__setattr__(self, "on_unavailable", mode)
        object.__setattr__(self, "fallback_provider", fallback)

    def as_dict(self) -> dict[str, Any]:
        return {
            "on_unavailable": self.on_unavailable.value,
            "fallback_provider": self.fallback_provider.value if self.fallback_provider else None,
        }


@dataclass(frozen=True, slots=True)
class JsonVectorStoreConfig:
    index_path: Path = Path(".rag/codecompass/vector_index.json")

    def __post_init__(self) -> None:
        raw = str(self.index_path or "").strip()
        if not raw or "\x00" in raw:
            raise VectorStoreConfigError("invalid_json_vector_store_index_path")
        object.__setattr__(self, "index_path", Path(raw))

    def as_dict(self) -> dict[str, str]:
        return {"index_path": str(self.index_path)}


@dataclass(frozen=True, slots=True)
class QdrantEndpointConfig:
    rest_url: str = "http://localhost:6333"
    grpc_url: str | None = None
    api_key_ref: str | None = None
    tls_ca_cert_ref: str | None = None
    allowed_origins: tuple[str, ...] = ("http://localhost:6333",)
    trusted_private_origins: tuple[str, ...] = ()
    external_calls_allowed: bool = False
    connect_timeout_seconds: float = 3.0
    timeout_seconds: float = 10.0
    prefer_grpc: bool = False
    tls_verify: bool = True

    def __post_init__(self) -> None:
        external_calls_allowed = _strict_bool(
            self.external_calls_allowed,
            cause_reason="invalid_qdrant_external_calls_allowed",
        )
        prefer_grpc = _strict_bool(
            self.prefer_grpc,
            cause_reason="invalid_qdrant_prefer_grpc",
        )
        tls_verify = _strict_bool(
            self.tls_verify,
            cause_reason="invalid_qdrant_tls_verify",
        )
        connect_timeout = _strict_timeout(self.connect_timeout_seconds)
        timeout = _strict_timeout(self.timeout_seconds)
        if not 0.05 <= connect_timeout <= 300.0 or not 0.05 <= timeout <= 300.0:
            raise VectorStoreConfigError(
                "vector_store_invalid_timeout",
                cause_reason="invalid_qdrant_timeout_seconds",
            )
        if not tls_verify:
            raise VectorStoreConfigError(
                "vector_store_tls_policy_violation",
                cause_reason="qdrant_tls_verification_cannot_be_disabled",
            )
        allowed_values = _origin_sequence(
            self.allowed_origins,
            allow_empty=False,
            cause_reason="invalid_qdrant_allowed_origins",
        )
        trusted_private_values = _origin_sequence(
            self.trusted_private_origins,
            allow_empty=True,
            cause_reason="invalid_qdrant_trusted_private_origins",
        )
        try:
            allowed = normalize_allowed_origins(allowed_values)
            trusted_private = normalize_trusted_private_origins(
                trusted_private_values,
                allowed_origins=allowed,
            )
            rest = validate_endpoint_access(
                self.rest_url,
                transport="rest",
                allowed_origins=allowed,
                external_calls_allowed=external_calls_allowed,
                trusted_private_origins=trusted_private,
            )
            grpc = None
            if self.grpc_url:
                grpc = validate_endpoint_access(
                    self.grpc_url,
                    transport="grpc",
                    allowed_origins=allowed,
                    external_calls_allowed=external_calls_allowed,
                    trusted_private_origins=trusted_private,
                )
        except VectorStoreEndpointPolicyError as exc:
            if exc.reason in {
                "vector_store_remote_rest_tls_required",
                "vector_store_remote_grpc_tls_required",
            }:
                raise VectorStoreConfigError(
                    "vector_store_tls_policy_violation",
                    cause_reason=exc.reason,
                ) from exc
            raise VectorStoreConfigError(
                "vector_store_invalid_origin",
                cause_reason=exc.reason,
            ) from exc
        if prefer_grpc and grpc is None:
            raise VectorStoreConfigError(
                "vector_store_invalid_origin",
                cause_reason="qdrant_grpc_url_required",
            )
        secret_ref = None
        if self.api_key_ref:
            try:
                secret_ref = SecretReference.parse(self.api_key_ref).as_uri()
            except VectorStoreEndpointPolicyError as exc:
                raise VectorStoreConfigError(
                    "vector_store_secret_scheme_rejected",
                    cause_reason=exc.reason,
                ) from exc
        tls_ca_cert_ref = None
        if self.tls_ca_cert_ref:
            try:
                parsed_ca_ref = SecretReference.parse(self.tls_ca_cert_ref)
                if parsed_ca_ref.scheme != "secretfile":
                    raise VectorStoreEndpointPolicyError(
                        "qdrant_tls_ca_cert_ref_must_be_secretfile"
                    )
                tls_ca_cert_ref = parsed_ca_ref.as_uri()
            except VectorStoreEndpointPolicyError as exc:
                raise VectorStoreConfigError(
                    "vector_store_tls_policy_violation",
                    cause_reason=exc.reason,
                ) from exc
            if not rest.secure and not (grpc and grpc.secure):
                raise VectorStoreConfigError(
                    "vector_store_tls_policy_violation",
                    cause_reason="qdrant_tls_ca_requires_secure_endpoint",
                )
        object.__setattr__(self, "rest_url", rest.origin)
        object.__setattr__(self, "grpc_url", grpc.origin if grpc else None)
        object.__setattr__(self, "api_key_ref", secret_ref)
        object.__setattr__(self, "tls_ca_cert_ref", tls_ca_cert_ref)
        object.__setattr__(self, "allowed_origins", allowed)
        object.__setattr__(
            self,
            "trusted_private_origins",
            trusted_private,
        )
        object.__setattr__(self, "external_calls_allowed", external_calls_allowed)
        object.__setattr__(self, "connect_timeout_seconds", connect_timeout)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "prefer_grpc", prefer_grpc)
        object.__setattr__(self, "tls_verify", True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rest_url": self.rest_url,
            "grpc_url": self.grpc_url,
            "api_key_ref": self.api_key_ref,
            "tls_ca_cert_ref": self.tls_ca_cert_ref,
            "allowed_origins": list(self.allowed_origins),
            "trusted_private_origins": list(self.trusted_private_origins),
            "external_calls_allowed": self.external_calls_allowed,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "prefer_grpc": self.prefer_grpc,
            "tls_verify": self.tls_verify,
        }

    @property
    def request_timeout_seconds(self) -> float:
        """Canonical request timeout; ``timeout_seconds`` remains a read-compatible alias."""

        return self.timeout_seconds

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QdrantEndpointConfig":
        payload = dict(value or {})
        if "api_key" in payload:
            raise VectorStoreConfigError(
                "vector_store_plaintext_secret_rejected",
                cause_reason="plaintext_qdrant_api_key_forbidden",
            )
        _reject_unknown(
            payload,
            {
                "rest_url",
                "url",
                "grpc_url",
                "api_key_ref",
                "api_key_env",
                "tls_ca_cert_ref",
                "allowed_origins",
                "allowed_base_urls",
                "trusted_private_origins",
                "external_calls_allowed",
                "connect_timeout_seconds",
                "request_timeout_seconds",
                "timeout_seconds",
                "prefer_grpc",
                "tls_verify",
            },
            "unknown_qdrant_endpoint_config_fields",
        )
        api_key_ref = payload.get("api_key_ref")
        if not api_key_ref and payload.get("api_key_env"):
            api_key_ref = f"env://{str(payload['api_key_env']).strip()}"
        request_timeout = payload.get("request_timeout_seconds")
        legacy_timeout = payload.get("timeout_seconds")
        if request_timeout is not None:
            request_timeout = _strict_timeout(request_timeout)
        if legacy_timeout is not None:
            legacy_timeout = _strict_timeout(legacy_timeout)
        if (
            request_timeout is not None
            and legacy_timeout is not None
            and request_timeout != legacy_timeout
        ):
            raise VectorStoreConfigError(
                "vector_store_invalid_timeout",
                cause_reason="conflicting_qdrant_request_timeout",
            )
        if "allowed_origins" in payload:
            allowed_origins = _origin_sequence(
                payload["allowed_origins"],
                allow_empty=False,
                cause_reason="invalid_qdrant_allowed_origins",
            )
        elif "allowed_base_urls" in payload:
            allowed_origins = _origin_sequence(
                payload["allowed_base_urls"],
                allow_empty=False,
                cause_reason="invalid_qdrant_allowed_origins",
            )
        else:
            allowed_origins = ("http://localhost:6333",)
        trusted_private_origins = _origin_sequence(
            payload.get("trusted_private_origins", ()),
            allow_empty=True,
            cause_reason="invalid_qdrant_trusted_private_origins",
        )
        return cls(
            rest_url=str(payload.get("rest_url") or payload.get("url") or "http://localhost:6333"),
            grpc_url=str(payload["grpc_url"]) if payload.get("grpc_url") else None,
            api_key_ref=str(api_key_ref) if api_key_ref else None,
            tls_ca_cert_ref=(
                str(payload["tls_ca_cert_ref"])
                if payload.get("tls_ca_cert_ref")
                else None
            ),
            allowed_origins=allowed_origins,
            trusted_private_origins=trusted_private_origins,
            external_calls_allowed=payload.get("external_calls_allowed", False),
            connect_timeout_seconds=payload.get("connect_timeout_seconds", 3.0),
            timeout_seconds=(
                request_timeout
                if request_timeout is not None
                else legacy_timeout
                if legacy_timeout is not None
                else 10.0
            ),
            prefer_grpc=payload.get("prefer_grpc", False),
            tls_verify=payload.get("tls_verify", True),
        )


_COLLECTION_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
_SCHEMA_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class QdrantVectorStoreConfig:
    endpoint: QdrantEndpointConfig = field(default_factory=QdrantEndpointConfig)
    collection_prefix: str = "ananta"
    collection_strategy: str = "workspace_repository_profile"
    distance: VectorStoreDistance = VectorStoreDistance.COSINE
    rebuild_strategy: str = "versioned_collection_alias_swap"
    schema_version: str = "qdrant_vector_store.v1"
    retention_collections: int = 2
    store_embedding_text: bool = False

    def __post_init__(self) -> None:
        prefix = str(self.collection_prefix or "").strip()
        if not _COLLECTION_PREFIX.fullmatch(prefix):
            raise VectorStoreConfigError(
                "vector_store_invalid_collection",
                cause_reason="invalid_qdrant_collection_prefix",
            )
        if self.collection_strategy != "workspace_repository_profile":
            raise VectorStoreConfigError(
                "vector_store_invalid_collection",
                cause_reason="unsupported_qdrant_collection_strategy",
            )
        distance = (
            self.distance
            if isinstance(self.distance, VectorStoreDistance)
            else _enum_value(
                VectorStoreDistance,
                self.distance,
                "vector_store_invalid_distance",
                cause_reason="unsupported_qdrant_distance",
            )
        )
        if self.rebuild_strategy != "versioned_collection_alias_swap":
            raise VectorStoreConfigError(
                "vector_store_invalid_collection",
                cause_reason="unsupported_qdrant_rebuild_strategy",
            )
        if not isinstance(self.schema_version, str) or not _SCHEMA_VERSION.fullmatch(
            self.schema_version
        ):
            raise VectorStoreConfigError(
                "vector_store_invalid_collection",
                cause_reason="invalid_qdrant_schema_version",
            )
        if isinstance(self.retention_collections, bool) or not isinstance(
            self.retention_collections,
            int,
        ):
            raise VectorStoreConfigError(
                "vector_store_invalid_collection",
                cause_reason="invalid_qdrant_retention_collections",
            )
        retention = self.retention_collections
        if not 1 <= retention <= 32:
            raise VectorStoreConfigError(
                "vector_store_invalid_collection",
                cause_reason="invalid_qdrant_retention_collections",
            )
        object.__setattr__(self, "collection_prefix", prefix)
        object.__setattr__(self, "distance", distance)
        object.__setattr__(self, "retention_collections", retention)
        object.__setattr__(
            self,
            "store_embedding_text",
            _strict_bool(
                self.store_embedding_text,
                cause_reason="invalid_qdrant_store_embedding_text",
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint.as_dict(),
            "collection_prefix": self.collection_prefix,
            "collection_strategy": self.collection_strategy,
            "distance": self.distance.value,
            "rebuild_strategy": self.rebuild_strategy,
            "schema_version": self.schema_version,
            "retention_collections": self.retention_collections,
            "store_embedding_text": self.store_embedding_text,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QdrantVectorStoreConfig":
        payload = dict(value or {})
        endpoint_keys = {
            "rest_url",
            "url",
            "grpc_url",
            "api_key_ref",
            "api_key_env",
            "api_key",
            "tls_ca_cert_ref",
            "allowed_origins",
            "allowed_base_urls",
            "trusted_private_origins",
            "external_calls_allowed",
            "connect_timeout_seconds",
            "request_timeout_seconds",
            "timeout_seconds",
            "prefer_grpc",
            "tls_verify",
        }
        _reject_unknown(
            payload,
            endpoint_keys
            | {
                "endpoint",
                "collection_prefix",
                "collection_strategy",
                "distance",
                "rebuild_strategy",
                "schema_version",
                "retention_collections",
                "store_embedding_text",
                "on_unavailable",
            },
            "unknown_qdrant_config_fields",
        )
        endpoint_payload = dict(payload.get("endpoint") or {})
        endpoint_payload.update({key: payload[key] for key in endpoint_keys if key in payload})
        return cls(
            endpoint=QdrantEndpointConfig.from_mapping(endpoint_payload),
            collection_prefix=str(payload.get("collection_prefix") or "ananta"),
            collection_strategy=str(payload.get("collection_strategy") or "workspace_repository_profile"),
            distance=_enum_value(
                VectorStoreDistance,
                payload.get("distance", "cosine"),
                "vector_store_invalid_distance",
                cause_reason="unsupported_qdrant_distance",
            ),
            rebuild_strategy=str(payload.get("rebuild_strategy") or "versioned_collection_alias_swap"),
            schema_version=payload.get(
                "schema_version",
                "qdrant_vector_store.v1",
            ),
            retention_collections=payload.get("retention_collections", 2),
            store_embedding_text=payload.get("store_embedding_text", False),
        )


@dataclass(frozen=True, slots=True)
class VectorStoreConfig:
    provider: VectorStoreProvider = VectorStoreProvider.JSON
    availability: AvailabilityPolicy = field(default_factory=AvailabilityPolicy)
    json: JsonVectorStoreConfig = field(default_factory=JsonVectorStoreConfig)
    qdrant: QdrantVectorStoreConfig | None = None
    duckdb: Any | None = None

    def __post_init__(self) -> None:
        provider = (
            self.provider
            if isinstance(self.provider, VectorStoreProvider)
            else _enum_value(
                VectorStoreProvider,
                self.provider,
                "vector_store_invalid_provider",
                cause_reason="unknown_vector_store_provider",
            )
        )
        if provider == VectorStoreProvider.QDRANT and self.qdrant is None:
            raise VectorStoreConfigError(
                "vector_store_invalid_provider",
                cause_reason="missing_qdrant_vector_store_config",
            )
        if provider == VectorStoreProvider.DUCKDB and self.duckdb is None:
            raise VectorStoreConfigError(
                "vector_store_invalid_provider",
                cause_reason="missing_duckdb_vector_store_config",
            )
        object.__setattr__(self, "provider", provider)

    @classmethod
    def for_json(cls, index_path: str | Path) -> "VectorStoreConfig":
        return cls(json=JsonVectorStoreConfig(index_path=Path(index_path)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "VectorStoreConfig":
        outer = dict(value or {})
        payload = dict(outer.get("vector_store") or outer)
        if {"fail_mode", "fallback_provider"} & set(payload):
            raise VectorStoreConfigError(
                "vector_store_invalid_availability_policy",
                cause_reason="legacy_availability_fields_not_supported",
            )
        _reject_unknown(
            payload,
            {"provider", "availability", "json", "qdrant", "duckdb"},
            "unknown_vector_store_config_fields",
        )
        provider = _enum_value(
            VectorStoreProvider,
            payload.get("provider", "json"),
            "vector_store_invalid_provider",
            cause_reason="unknown_vector_store_provider",
        )
        availability_payload = dict(payload.get("availability") or {})
        _reject_unknown(
            availability_payload,
            {"on_unavailable", "fallback_provider"},
            "vector_store_invalid_availability_policy",
        )
        on_unavailable = availability_payload.get(
            "on_unavailable",
            "degraded_empty",
        )
        fallback = availability_payload.get("fallback_provider")
        availability = AvailabilityPolicy(
            on_unavailable=_enum_value(
                AvailabilityMode,
                on_unavailable,
                "vector_store_invalid_availability_policy",
                cause_reason="invalid_vector_store_availability_mode",
            ),
            fallback_provider=(
                _enum_value(
                    VectorStoreProvider,
                    fallback,
                    "vector_store_invalid_availability_policy",
                    cause_reason="unknown_vector_store_fallback_provider",
                )
                if fallback
                else None
            ),
        )
        json_payload = dict(payload.get("json") or {})
        _reject_unknown(json_payload, {"index_path"}, "unknown_json_vector_store_config_fields")
        json_config = JsonVectorStoreConfig(
            index_path=Path(json_payload.get("index_path") or ".rag/codecompass/vector_index.json")
        )
        qdrant_config = (
            QdrantVectorStoreConfig.from_mapping(dict(payload.get("qdrant") or {}))
            if payload.get("qdrant") is not None
            else None
        )
        duckdb_config = None
        if payload.get("duckdb") is not None:
            from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig

            duckdb_config = DuckDBVectorStoreConfig.from_mapping(dict(payload.get("duckdb") or {}))
        elif provider == VectorStoreProvider.DUCKDB:
            from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig

            duckdb_config = DuckDBVectorStoreConfig()
        return cls(
            provider=provider,
            availability=availability,
            json=json_config,
            qdrant=qdrant_config,
            duckdb=duckdb_config,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "availability": self.availability.as_dict(),
            "json": self.json.as_dict(),
            "qdrant": self.qdrant.as_dict() if self.qdrant else None,
            "duckdb": self.duckdb.as_dict() if self.duckdb else None,
        }

    def config_hash(self) -> str:
        hashable = self.as_dict()
        qdrant = hashable.get("qdrant")
        if isinstance(qdrant, dict):
            endpoint = qdrant.get("endpoint")
            if isinstance(endpoint, dict):
                endpoint.pop("api_key_ref", None)
                endpoint.pop("tls_ca_cert_ref", None)
        canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "AvailabilityMode",
    "AvailabilityPolicy",
    "JsonVectorStoreConfig",
    "QdrantEndpointConfig",
    "QdrantVectorStoreConfig",
    "VectorStoreConfig",
    "VectorStoreConfigError",
    "VectorStoreDistance",
    "VectorStoreProvider",
]
