from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from worker.retrieval.qdrant_client_port import ClientPoint
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
)

VECTOR_POINT_SCHEMA_VERSION = "vector_store.v1"
QDRANT_BACKEND_SCHEMA_VERSION = "qdrant_vector_store.v1"
RECORD_TYPE_KEY = "_ananta_record_type"
RECORD_TYPE_RECORD = "record"
RECORD_TYPE_MANIFEST = "manifest"
POINT_NAMESPACE = uuid.UUID("0840c193-dfcb-58d1-ac1d-14306afaaed0")
MANIFEST_NAMESPACE = uuid.UUID("13b0e42d-15d9-58ad-95d7-1c69957bd073")
_TOKEN_RE = re.compile(r"[^a-z0-9_-]+")
MAX_EMBEDDING_TEXT_BYTES = 64 * 1024
MAX_KIND_BYTES = 128
MAX_FILE_BYTES = 4096
MAX_FILE_SEGMENTS = 128
MAX_PARENT_ID_BYTES = 512
MAX_SOURCE_SCOPE_BYTES = 128
MAX_ROLE_LABELS = 64
MAX_ROLE_LABEL_BYTES = 128
MAX_METADATA_BYTES = 16 * 1024
MAX_METADATA_DEPTH = 4
MAX_METADATA_ENTRIES = 256
MAX_METADATA_KEYS = 64
MAX_METADATA_KEY_BYTES = 128
MAX_METADATA_SEQUENCE_ITEMS = 64
MAX_METADATA_STRING_BYTES = 1024
MIN_IMPORTANCE_SCORE = 0.0
MAX_IMPORTANCE_SCORE = 1_000_000.0
VECTOR_PAYLOAD_INVALID = "vector_payload_invalid"
VECTOR_PAYLOAD_TOO_LARGE = "vector_payload_too_large"

_REQUIRED_COMPATIBILITY_FIELDS = (
    "provider",
    "model",
    "profile",
    "encoding",
    "config_hash",
    "schema_version",
    "manifest_hash",
)
_COMPATIBILITY_DIAGNOSTIC_FIELDS = (
    "backend_schema_version",
    "dimensions",
    "distance",
    "provider",
    "model",
    "profile",
    "encoding",
    "config_hash",
    "schema_version",
    "manifest_hash",
)
_SENSITIVE_METADATA_EXACT_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "authtoken",
        "body",
        "content",
        "credential",
        "credentials",
        "document",
        "embedding",
        "embeddingtext",
        "password",
        "passwd",
        "secret",
        "text",
        "token",
        "vector",
    }
)
_SENSITIVE_METADATA_KEY_FRAGMENTS = (
    "accesstoken",
    "apikey",
    "authtoken",
    "authorization",
    "credential",
    "documentcontent",
    "embeddingtext",
    "fullcontent",
    "fulltext",
    "password",
    "rawcontent",
    "refreshtoken",
    "sourcecode",
)
_SENSITIVE_DIAGNOSTIC_TEXT_RE = re.compile(
    r"(?:authorization|api[_-]?key|bearer|password|secret|token|://[^\s/]*@)",
    re.IGNORECASE,
)


class QdrantSchemaError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def canonical_scope(scope: VectorScope) -> str:
    if not isinstance(scope, VectorScope):
        raise QdrantSchemaError("vector_scope_required")
    values = (
        scope.workspace_id,
        scope.repository_id,
        scope.profile_name,
        scope.domain,
    )
    if any(not str(value or "").strip() for value in values):
        raise QdrantSchemaError("vector_scope_required")
    if any(len(str(value)) > 256 or any(ord(char) < 32 for char in str(value)) for value in values):
        raise QdrantSchemaError("vector_scope_invalid")
    return json.dumps(
        {
            "domain": str(scope.domain),
            "profile_name": str(scope.profile_name),
            "repository_id": str(scope.repository_id),
            "workspace_id": str(scope.workspace_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def scope_digest(scope: VectorScope) -> str:
    return hashlib.sha256(canonical_scope(scope).encode("utf-8")).hexdigest()[:20]


def deterministic_point_id(scope: VectorScope, record_id: str) -> str:
    clean_record_id = str(record_id or "").strip()
    if not clean_record_id:
        raise QdrantSchemaError("record_id_required")
    return str(uuid.uuid5(POINT_NAMESPACE, f"{canonical_scope(scope)}\n{clean_record_id}"))


def manifest_point_id(collection_name: str) -> str:
    return str(uuid.uuid5(MANIFEST_NAMESPACE, str(collection_name)))


def _normalise_distance(value: str) -> str:
    distance = str(value or "").lower()
    if distance not in {"cosine", "dot", "euclid", "manhattan"}:
        raise QdrantSchemaError("vector_store_invalid_distance")
    return distance


def compatibility_fingerprint(spec: CompatibilitySpec) -> str:
    payload = compatibility_payload(spec)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def compatibility_payload(spec: CompatibilitySpec) -> dict[str, Any]:
    if int(spec.dimensions) <= 0:
        raise QdrantSchemaError("dimensions_mismatch")
    return {
        "dimensions": int(spec.dimensions),
        "distance": _normalise_distance(spec.distance),
        "provider": str(spec.provider or ""),
        "model": str(spec.model or ""),
        "profile": str(spec.profile or ""),
        "encoding": str(spec.encoding or ""),
        "config_hash": str(spec.config_hash or ""),
        "schema_version": str(spec.schema_version or VECTOR_POINT_SCHEMA_VERSION),
        "manifest_hash": str(spec.manifest_hash or ""),
    }


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    compatible: bool
    reason: str
    expected: Mapping[str, Any]
    found: Mapping[str, Any]


def compare_compatibility(
    expected: CompatibilitySpec,
    found: Mapping[str, Any] | None,
) -> CompatibilityReport:
    expected_payload = compatibility_payload(expected)
    found_payload = dict(found or {})
    if not found_payload:
        return CompatibilityReport(False, "migration_required", expected_payload, {})
    checks = (
        ("dimensions", "dimensions_mismatch", True),
        ("distance", "distance_mismatch", True),
        ("provider", "provider_changed", False),
        ("model", "model_changed", False),
        ("profile", "profile_changed", False),
        ("encoding", "encoding_changed", False),
        ("config_hash", "config_changed", False),
        ("schema_version", "migration_required", True),
        ("manifest_hash", "manifest_changed", False),
    )
    for field_name, reason, always_compare in checks:
        expected_value = expected_payload.get(field_name)
        found_value = found_payload.get(field_name)
        if found_value in {None, ""} and (always_compare or expected_value not in {None, ""}):
            return CompatibilityReport(False, "migration_required", expected_payload, found_payload)
        if (always_compare or expected_value not in {None, ""}) and found_value != expected_value:
            return CompatibilityReport(False, reason, expected_payload, found_payload)
    return CompatibilityReport(True, "compatible", expected_payload, found_payload)


def missing_compatibility_fields(spec: CompatibilitySpec) -> tuple[str, ...]:
    payload = compatibility_payload(spec)
    return tuple(
        field_name
        for field_name in _REQUIRED_COMPATIBILITY_FIELDS
        if not str(payload.get(field_name) or "").strip()
    )


def compatibility_diagnostics(report: CompatibilityReport) -> dict[str, dict[str, Any]]:
    """Return a bounded, non-sensitive projection of a compatibility report."""

    return {
        "expected": _sanitise_compatibility_mapping(report.expected),
        "found": _sanitise_compatibility_mapping(report.found),
    }


def _sanitise_compatibility_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for field_name in _COMPATIBILITY_DIAGNOSTIC_FIELDS:
        raw_value = value.get(field_name)
        if field_name == "dimensions":
            if isinstance(raw_value, bool):
                continue
            try:
                dimensions = int(raw_value)
            except (TypeError, ValueError):
                continue
            if dimensions > 0:
                safe[field_name] = dimensions
            continue
        if not isinstance(raw_value, (str, int, float)):
            continue
        text = str(raw_value)
        if _SENSITIVE_DIAGNOSTIC_TEXT_RE.search(text):
            safe[field_name] = "redacted"
            continue
        encoded = text.encode("utf-8")
        if len(encoded) > 256:
            text = encoded[:256].decode("utf-8", errors="ignore")
        safe[field_name] = text
    return safe


def _file_prefixes(value: str) -> list[str]:
    cleaned = value.replace("\\", "/").strip("/")
    if not cleaned:
        return []
    parts = [part for part in cleaned.split("/") if part]
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def _bounded_payload_text(
    source: Mapping[str, Any],
    field_name: str,
    *,
    default: str,
    maximum_bytes: int,
    strip: bool = False,
) -> str:
    raw_value = source.get(field_name)
    if raw_value is None:
        return default
    if not isinstance(raw_value, str):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    value = raw_value.strip() if strip else raw_value
    if not value and default:
        return default
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    if len(value.encode("utf-8")) > maximum_bytes:
        raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
    return value


def _normalise_role_labels(value: Any) -> list[str]:
    if value is None:
        return []
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, SequenceABC)
    ):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    if len(value) > MAX_ROLE_LABELS:
        raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
    labels: list[str] = []
    for raw_label in value:
        if not isinstance(raw_label, str):
            raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
        label = raw_label.strip()
        if not label:
            continue
        if any(ord(character) < 32 or ord(character) == 127 for character in label):
            raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
        if len(label.encode("utf-8")) > MAX_ROLE_LABEL_BYTES:
            raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
        labels.append(label)
    return labels


def _normalise_importance_score(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    score = float(value)
    if (
        not math.isfinite(score)
        or score < MIN_IMPORTANCE_SCORE
        or score > MAX_IMPORTANCE_SCORE
    ):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    return score


def _metadata_key_is_sensitive(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return (
        compact in _SENSITIVE_METADATA_EXACT_KEYS
        or compact.startswith("secret")
        or compact.endswith("secret")
        or any(fragment in compact for fragment in _SENSITIVE_METADATA_KEY_FRAGMENTS)
    )


def _normalise_metadata_value(
    value: Any,
    *,
    depth: int,
    entry_budget: list[int],
) -> Any:
    if depth > MAX_METADATA_DEPTH:
        raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
    if isinstance(value, MappingABC):
        if len(value) > MAX_METADATA_KEYS:
            raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
        entry_budget[0] += len(value)
        if entry_budget[0] > MAX_METADATA_ENTRIES:
            raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
        result: dict[str, Any] = {}
        for raw_key, raw_child in value.items():
            if not isinstance(raw_key, str):
                raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
            key = raw_key.strip()
            if not key or any(
                ord(character) < 32 or ord(character) == 127
                for character in key
            ):
                raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
            if len(key.encode("utf-8")) > MAX_METADATA_KEY_BYTES:
                raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
            if _metadata_key_is_sensitive(key):
                continue
            result[key] = _normalise_metadata_value(
                raw_child,
                depth=depth + 1,
                entry_budget=entry_budget,
            )
        return result
    if isinstance(value, SequenceABC) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        if len(value) > MAX_METADATA_SEQUENCE_ITEMS:
            raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
        entry_budget[0] += len(value)
        if entry_budget[0] > MAX_METADATA_ENTRIES:
            raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
        return [
            _normalise_metadata_value(
                item,
                depth=depth + 1,
                entry_budget=entry_budget,
            )
            for item in value
        ]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_METADATA_STRING_BYTES:
            raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
        return value
    raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)


def sanitise_payload_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, MappingABC):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    normalised = _normalise_metadata_value(
        value,
        depth=0,
        entry_budget=[0],
    )
    if len(
        json.dumps(
            normalised,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) > MAX_METADATA_BYTES:
        raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
    return normalised


def normalise_embedding_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise QdrantSchemaError(VECTOR_PAYLOAD_INVALID)
    if len(value.encode("utf-8")) > MAX_EMBEDDING_TEXT_BYTES:
        raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
    return value


def point_payload(
    point: PreparedVectorPoint,
    compatibility: CompatibilitySpec,
    *,
    store_embedding_text: bool = False,
) -> dict[str, Any]:
    vector = tuple(float(value) for value in point.vector)
    if len(vector) != int(compatibility.dimensions) or any(not math.isfinite(value) for value in vector):
        raise QdrantSchemaError("dimensions_mismatch")
    scope = point.scope
    canonical_scope(scope)
    source = dict(point.payload or {})
    source_hash = str(point.source_hash or "").strip()
    if not source_hash:
        raise QdrantSchemaError("missing_source_hash")
    payload: dict[str, Any] = {
        RECORD_TYPE_KEY: RECORD_TYPE_RECORD,
        "workspace_id": str(scope.workspace_id),
        "repository_id": str(scope.repository_id),
        "profile_name": str(scope.profile_name),
        "domain": str(scope.domain),
        "record_id": str(point.record_id),
        "source_hash": source_hash,
        "schema_version": str(compatibility.schema_version or VECTOR_POINT_SCHEMA_VERSION),
        "provider": str(compatibility.provider or ""),
        "model": str(compatibility.model or ""),
        "encoding": str(compatibility.encoding or ""),
        "config_hash": str(compatibility.config_hash or ""),
        "manifest_hash": str(compatibility.manifest_hash or ""),
        "kind": _bounded_payload_text(
            source,
            "kind",
            default="unknown",
            maximum_bytes=MAX_KIND_BYTES,
            strip=True,
        ),
        "file": _bounded_payload_text(
            source,
            "file",
            default="",
            maximum_bytes=MAX_FILE_BYTES,
        ),
        "parent_id": _bounded_payload_text(
            source,
            "parent_id",
            default="",
            maximum_bytes=MAX_PARENT_ID_BYTES,
        ),
        "source_scope": _bounded_payload_text(
            source,
            "source_scope",
            default="repo",
            maximum_bytes=MAX_SOURCE_SCOPE_BYTES,
            strip=True,
        ),
        "role_labels": _normalise_role_labels(source.get("role_labels")),
        "importance_score": _normalise_importance_score(
            source.get("importance_score")
        ),
    }
    if len([part for part in payload["file"].replace("\\", "/").split("/") if part]) > (
        MAX_FILE_SEGMENTS
    ):
        raise QdrantSchemaError(VECTOR_PAYLOAD_TOO_LARGE)
    payload["file_prefixes"] = _file_prefixes(payload["file"])
    payload["metadata"] = sanitise_payload_metadata(source.get("metadata"))
    if store_embedding_text:
        embedding_text = normalise_embedding_text(source.get("embedding_text"))
        if embedding_text:
            payload["embedding_text"] = embedding_text
    return payload


def to_client_point(
    point: PreparedVectorPoint,
    compatibility: CompatibilitySpec,
    *,
    store_embedding_text: bool = False,
) -> ClientPoint:
    point_id = _validated_point_id(point)
    return ClientPoint(
        point_id=point_id,
        vector=tuple(float(value) for value in point.vector),
        payload=point_payload(
            point,
            compatibility,
            store_embedding_text=store_embedding_text,
        ),
    )


def _validated_point_id(point: PreparedVectorPoint) -> str:
    expected = deterministic_point_id(point.scope, point.record_id)
    if point.point_id is not None and point.point_id != expected:
        raise QdrantSchemaError("vector_point_id_mismatch")
    return expected


def _safe_token(value: str, *, fallback: str) -> str:
    token = _TOKEN_RE.sub("-", str(value or "").strip().lower()).strip("-_")
    return (token or fallback)[:24]


def collection_alias(prefix: str, scope: VectorScope) -> str:
    safe_prefix = _safe_token(prefix, fallback="ananta")
    domain = _safe_token(scope.domain, fallback="dense")
    return f"{safe_prefix}-{domain}-{scope_digest(scope)}"


def versioned_collection_name(prefix: str, scope: VectorScope, index_version: str) -> str:
    clean_version = str(index_version or "").strip()
    if not clean_version:
        raise QdrantSchemaError("index_version_required")
    version_digest = hashlib.sha256(clean_version.encode("utf-8")).hexdigest()[:16]
    return f"{collection_alias(prefix, scope)}-{version_digest}"


def staging_collection_name(
    prefix: str,
    scope: VectorScope,
    index_version: str,
    staging_token: str,
) -> str:
    token = str(staging_token or "").strip()
    if not token:
        raise QdrantSchemaError("staging_token_required")
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return (
        f"{versioned_collection_name(prefix, scope, index_version)}"
        f"-staging-{token_digest}"
    )


def manifest_client_point(
    collection_name: str,
    scope: VectorScope,
    compatibility: CompatibilitySpec,
    *,
    created_at_epoch: float,
    backend_schema_version: str = QDRANT_BACKEND_SCHEMA_VERSION,
) -> ClientPoint:
    payload = {
        RECORD_TYPE_KEY: RECORD_TYPE_MANIFEST,
        "backend_schema_version": str(backend_schema_version),
        "scope": json.loads(canonical_scope(scope)),
        "compatibility": compatibility_payload(compatibility),
        "created_at_epoch": float(created_at_epoch),
    }
    return ClientPoint(
        point_id=manifest_point_id(collection_name),
        vector=tuple(0.0 for _ in range(int(compatibility.dimensions))),
        payload=payload,
    )


def scope_matches_payload(scope: VectorScope, payload: Mapping[str, Any]) -> bool:
    return all(
        str(payload.get(field_name) or "") == str(expected)
        for field_name, expected in (
            ("workspace_id", scope.workspace_id),
            ("repository_id", scope.repository_id),
            ("profile_name", scope.profile_name),
            ("domain", scope.domain),
        )
    )


def unique_point_count(points: Sequence[PreparedVectorPoint]) -> int:
    return len({_validated_point_id(point) for point in points})
