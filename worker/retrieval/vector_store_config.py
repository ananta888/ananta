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
    validate_endpoint_access,
)


class VectorStoreConfigError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "invalid_vector_store_config")
        super().__init__(self.reason)


class VectorStoreProvider(str, Enum):
    JSON = "json"
    QDRANT = "qdrant"


class VectorStoreDistance(str, Enum):
    COSINE = "cosine"
    DOT = "dot"
    EUCLID = "euclid"


class AvailabilityMode(str, Enum):
    FAIL_FAST = "fail_fast"
    DEGRADED_EMPTY = "degraded_empty"
    EXPLICIT_JSON_FALLBACK = "explicit_json_fallback"


def _enum_value(enum_type: type[Enum], value: object, reason: str) -> Any:
    try:
        return enum_type(str(value).strip().lower())
    except ValueError as exc:
        raise VectorStoreConfigError(reason) from exc


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], reason: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise VectorStoreConfigError(f"{reason}:{','.join(unknown)}")


@dataclass(frozen=True, slots=True)
class AvailabilityPolicy:
    on_unavailable: AvailabilityMode = AvailabilityMode.DEGRADED_EMPTY
    fallback_provider: VectorStoreProvider | None = None

    def __post_init__(self) -> None:
        mode = (
            self.on_unavailable
            if isinstance(self.on_unavailable, AvailabilityMode)
            else _enum_value(AvailabilityMode, self.on_unavailable, "invalid_vector_store_availability_mode")
        )
        fallback = self.fallback_provider
        if fallback is not None and not isinstance(fallback, VectorStoreProvider):
            fallback = _enum_value(VectorStoreProvider, fallback, "unknown_vector_store_fallback_provider")
        if mode == AvailabilityMode.EXPLICIT_JSON_FALLBACK and fallback not in {None, VectorStoreProvider.JSON}:
            raise VectorStoreConfigError("explicit_fallback_requires_json_provider")
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
    allowed_origins: tuple[str, ...] = ("http://localhost:6333",)
    external_calls_allowed: bool = False
    timeout_seconds: float = 10.0
    prefer_grpc: bool = False
    tls_verify: bool = True

    def __post_init__(self) -> None:
        timeout = float(self.timeout_seconds)
        if not 0.05 <= timeout <= 300.0:
            raise VectorStoreConfigError("invalid_qdrant_timeout_seconds")
        if not bool(self.tls_verify):
            raise VectorStoreConfigError("qdrant_tls_verification_cannot_be_disabled")
        try:
            allowed = normalize_allowed_origins(self.allowed_origins)
            rest = validate_endpoint_access(
                self.rest_url,
                transport="rest",
                allowed_origins=allowed,
                external_calls_allowed=bool(self.external_calls_allowed),
            )
            grpc = None
            if self.grpc_url:
                grpc = validate_endpoint_access(
                    self.grpc_url,
                    transport="grpc",
                    allowed_origins=allowed,
                    external_calls_allowed=bool(self.external_calls_allowed),
                )
        except VectorStoreEndpointPolicyError as exc:
            raise VectorStoreConfigError(exc.reason) from exc
        if bool(self.prefer_grpc) and grpc is None:
            raise VectorStoreConfigError("qdrant_grpc_url_required")
        secret_ref = None
        if self.api_key_ref:
            try:
                secret_ref = SecretReference.parse(self.api_key_ref).as_uri()
            except VectorStoreEndpointPolicyError as exc:
                raise VectorStoreConfigError(exc.reason) from exc
        object.__setattr__(self, "rest_url", rest.origin)
        object.__setattr__(self, "grpc_url", grpc.origin if grpc else None)
        object.__setattr__(self, "api_key_ref", secret_ref)
        object.__setattr__(self, "allowed_origins", allowed)
        object.__setattr__(self, "external_calls_allowed", bool(self.external_calls_allowed))
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "prefer_grpc", bool(self.prefer_grpc))
        object.__setattr__(self, "tls_verify", True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rest_url": self.rest_url,
            "grpc_url": self.grpc_url,
            "api_key_ref": self.api_key_ref,
            "allowed_origins": list(self.allowed_origins),
            "external_calls_allowed": self.external_calls_allowed,
            "timeout_seconds": self.timeout_seconds,
            "prefer_grpc": self.prefer_grpc,
            "tls_verify": self.tls_verify,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QdrantEndpointConfig":
        payload = dict(value or {})
        if "api_key" in payload:
            raise VectorStoreConfigError("plaintext_qdrant_api_key_forbidden")
        _reject_unknown(
            payload,
            {
                "rest_url",
                "url",
                "grpc_url",
                "api_key_ref",
                "api_key_env",
                "allowed_origins",
                "allowed_base_urls",
                "external_calls_allowed",
                "timeout_seconds",
                "prefer_grpc",
                "tls_verify",
            },
            "unknown_qdrant_endpoint_config_fields",
        )
        api_key_ref = payload.get("api_key_ref")
        if not api_key_ref and payload.get("api_key_env"):
            api_key_ref = f"env://{str(payload['api_key_env']).strip()}"
        return cls(
            rest_url=str(payload.get("rest_url") or payload.get("url") or "http://localhost:6333"),
            grpc_url=str(payload["grpc_url"]) if payload.get("grpc_url") else None,
            api_key_ref=str(api_key_ref) if api_key_ref else None,
            allowed_origins=tuple(
                str(item)
                for item in (
                    payload.get("allowed_origins")
                    or payload.get("allowed_base_urls")
                    or ("http://localhost:6333",)
                )
            ),
            external_calls_allowed=bool(payload.get("external_calls_allowed", False)),
            timeout_seconds=float(payload.get("timeout_seconds", 10.0)),
            prefer_grpc=bool(payload.get("prefer_grpc", False)),
            tls_verify=bool(payload.get("tls_verify", True)),
        )


_COLLECTION_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


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
            raise VectorStoreConfigError("invalid_qdrant_collection_prefix")
        if self.collection_strategy != "workspace_repository_profile":
            raise VectorStoreConfigError("unsupported_qdrant_collection_strategy")
        distance = (
            self.distance
            if isinstance(self.distance, VectorStoreDistance)
            else _enum_value(VectorStoreDistance, self.distance, "unsupported_qdrant_distance")
        )
        if self.rebuild_strategy != "versioned_collection_alias_swap":
            raise VectorStoreConfigError("unsupported_qdrant_rebuild_strategy")
        retention = int(self.retention_collections)
        if not 1 <= retention <= 32:
            raise VectorStoreConfigError("invalid_qdrant_retention_collections")
        object.__setattr__(self, "collection_prefix", prefix)
        object.__setattr__(self, "distance", distance)
        object.__setattr__(self, "retention_collections", retention)
        object.__setattr__(self, "store_embedding_text", bool(self.store_embedding_text))

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
            "allowed_origins",
            "allowed_base_urls",
            "external_calls_allowed",
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
                "unsupported_qdrant_distance",
            ),
            rebuild_strategy=str(payload.get("rebuild_strategy") or "versioned_collection_alias_swap"),
            schema_version=str(payload.get("schema_version") or "qdrant_vector_store.v1"),
            retention_collections=int(payload.get("retention_collections", 2)),
            store_embedding_text=bool(payload.get("store_embedding_text", False)),
        )


@dataclass(frozen=True, slots=True)
class VectorStoreConfig:
    provider: VectorStoreProvider = VectorStoreProvider.JSON
    availability: AvailabilityPolicy = field(default_factory=AvailabilityPolicy)
    json: JsonVectorStoreConfig = field(default_factory=JsonVectorStoreConfig)
    qdrant: QdrantVectorStoreConfig | None = None

    def __post_init__(self) -> None:
        provider = (
            self.provider
            if isinstance(self.provider, VectorStoreProvider)
            else _enum_value(VectorStoreProvider, self.provider, "unknown_vector_store_provider")
        )
        if provider == VectorStoreProvider.QDRANT and self.qdrant is None:
            raise VectorStoreConfigError("missing_qdrant_vector_store_config")
        object.__setattr__(self, "provider", provider)

    @classmethod
    def for_json(cls, index_path: str | Path) -> "VectorStoreConfig":
        return cls(json=JsonVectorStoreConfig(index_path=Path(index_path)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "VectorStoreConfig":
        outer = dict(value or {})
        payload = dict(outer.get("vector_store") or outer)
        _reject_unknown(
            payload,
            {"provider", "availability", "fail_mode", "fallback_provider", "json", "qdrant"},
            "unknown_vector_store_config_fields",
        )
        provider = _enum_value(
            VectorStoreProvider,
            payload.get("provider", "json"),
            "unknown_vector_store_provider",
        )
        availability_payload = dict(payload.get("availability") or {})
        on_unavailable = availability_payload.get(
            "on_unavailable",
            payload.get("fail_mode", "degraded_empty"),
        )
        fallback = availability_payload.get("fallback_provider", payload.get("fallback_provider"))
        availability = AvailabilityPolicy(
            on_unavailable=_enum_value(
                AvailabilityMode,
                on_unavailable,
                "invalid_vector_store_availability_mode",
            ),
            fallback_provider=(
                _enum_value(
                    VectorStoreProvider,
                    fallback,
                    "unknown_vector_store_fallback_provider",
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
        return cls(
            provider=provider,
            availability=availability,
            json=json_config,
            qdrant=qdrant_config,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "availability": self.availability.as_dict(),
            "json": self.json.as_dict(),
            "qdrant": self.qdrant.as_dict() if self.qdrant else None,
        }

    def config_hash(self) -> str:
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
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
