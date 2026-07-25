from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from worker.retrieval.qdrant_client_port import ClientPoint
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
)


VECTOR_POINT_SCHEMA_VERSION = "vector_store.v1"
RECORD_TYPE_KEY = "_ananta_record_type"
RECORD_TYPE_RECORD = "record"
RECORD_TYPE_MANIFEST = "manifest"
POINT_NAMESPACE = uuid.UUID("0840c193-dfcb-58d1-ac1d-14306afaaed0")
MANIFEST_NAMESPACE = uuid.UUID("13b0e42d-15d9-58ad-95d7-1c69957bd073")
_TOKEN_RE = re.compile(r"[^a-z0-9_-]+")


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
        ("schema_version", "schema_mismatch", True),
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


def _file_prefixes(value: str) -> list[str]:
    cleaned = str(value or "").replace("\\", "/").strip("/")
    if not cleaned:
        return []
    parts = [part for part in cleaned.split("/") if part]
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def point_payload(point: PreparedVectorPoint, compatibility: CompatibilitySpec) -> dict[str, Any]:
    vector = tuple(float(value) for value in point.vector)
    if len(vector) != int(compatibility.dimensions) or any(not math.isfinite(value) for value in vector):
        raise QdrantSchemaError("dimensions_mismatch")
    scope = point.scope
    canonical_scope(scope)
    source = dict(point.payload or {})
    payload: dict[str, Any] = {
        RECORD_TYPE_KEY: RECORD_TYPE_RECORD,
        "workspace_id": str(scope.workspace_id),
        "repository_id": str(scope.repository_id),
        "profile_name": str(scope.profile_name),
        "domain": str(scope.domain),
        "record_id": str(point.record_id),
        "source_hash": str(point.source_hash or ""),
        "schema_version": str(compatibility.schema_version or VECTOR_POINT_SCHEMA_VERSION),
        "provider": str(compatibility.provider or ""),
        "model": str(compatibility.model or ""),
        "encoding": str(compatibility.encoding or ""),
        "config_hash": str(compatibility.config_hash or ""),
        "manifest_hash": str(compatibility.manifest_hash or ""),
        "kind": str(source.get("kind") or ""),
        "file": str(source.get("file") or ""),
        "parent_id": str(source.get("parent_id") or ""),
        "source_scope": str(source.get("source_scope") or "repo"),
        "role_labels": [
            str(value)
            for value in tuple(source.get("role_labels") or ())
            if str(value).strip()
        ][:64],
        "importance_score": float(source.get("importance_score") or 0.0),
    }
    payload["file_prefixes"] = _file_prefixes(payload["file"])
    safe_metadata = dict(source.get("metadata") or {})
    payload["metadata"] = {
        str(key): value
        for key, value in safe_metadata.items()
        if str(key) not in {"vector", "embedding_text", "api_key", "authorization"}
    }
    return payload


def to_client_point(point: PreparedVectorPoint, compatibility: CompatibilitySpec) -> ClientPoint:
    point_id = str(point.point_id or deterministic_point_id(point.scope, point.record_id))
    return ClientPoint(
        point_id=point_id,
        vector=tuple(float(value) for value in point.vector),
        payload=point_payload(point, compatibility),
    )


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


def manifest_client_point(
    collection_name: str,
    scope: VectorScope,
    compatibility: CompatibilitySpec,
    *,
    created_at_epoch: float,
) -> ClientPoint:
    payload = {
        RECORD_TYPE_KEY: RECORD_TYPE_MANIFEST,
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
    return len(
        {
            str(point.point_id or deterministic_point_id(point.scope, point.record_id))
            for point in points
        }
    )
