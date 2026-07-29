"""Validated contracts for Hub-owned vector-index mutation tasks."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_input_loader import (
    VectorIndexInputError,
    VectorIndexInputReference,
)
from worker.retrieval.vector_index_preparation import (
    VectorIndexPreparationSpec,
)
from worker.retrieval.vector_store_contract import CompatibilitySpec

VECTOR_INDEX_TASK_SCHEMA = "ananta.vector_index_task.v1"
VECTOR_INDEX_RESULT_SCHEMA = "ananta.vector_index_task_result.v1"
VECTOR_INDEX_OPERATIONS = frozenset({"index", "refresh", "rebuild", "delete", "migrate"})

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_SCOPE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_FIELDS = frozenset(
    {
        "points",
        "point_ids",
        "input_ref",
        "preparation",
        "compatibility",
        "migration",
        "batch_size",
        "delete_all_scope",
    }
)
_SECRET_MARKERS = ("api_key", "password", "secret", "token", "authorization")
_SAFE_SECRET_SUFFIXES = ("_ref", "_file", "_env")
_COMPATIBILITY_FIELDS = frozenset(
    {
        "dimensions",
        "distance",
        "provider",
        "model",
        "profile",
        "encoding",
        "config_hash",
        "schema_version",
        "manifest_hash",
    }
)
_COMPATIBILITY_TEXT_FIELDS = tuple(sorted(_COMPATIBILITY_FIELDS - {"dimensions"}))


def canonical_json(value: Any) -> bytes:
    """Return the stable JSON representation used for task fingerprints."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def clone_json(value: Any) -> Any:
    """Clone a JSON-compatible value without retaining caller-owned objects."""

    return json.loads(canonical_json(value))


def validate_idempotency_key(value: str) -> str:
    """Validate and normalize a caller-provided idempotency key."""

    candidate = str(value or "").strip()
    if _IDEMPOTENCY_KEY.fullmatch(candidate) is None:
        raise ValueError("vector_index_idempotency_key_invalid")
    return candidate


def _contains_plaintext_secret(value: Any, *, key: str = "") -> bool:
    normalized = str(key or "").strip().lower()
    if normalized and any(marker in normalized for marker in _SECRET_MARKERS):
        if not normalized.endswith(_SAFE_SECRET_SUFFIXES):
            return True
    if isinstance(value, Mapping):
        return any(_contains_plaintext_secret(item, key=str(item_key)) for item_key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_plaintext_secret(item) for item in value)
    return False


def _scope_value(value: str, *, field: str) -> str:
    candidate = str(value or "").strip()
    if _SCOPE_VALUE.fullmatch(candidate) is None:
        raise ValueError(f"vector_index_{field}_invalid")
    return candidate


def _complete_compatibility(
    raw: Mapping[str, Any] | None,
    *,
    required: bool,
) -> dict[str, Any] | None:
    if raw is None:
        if required:
            raise ValueError("vector_index_compatibility_required")
        return None
    payload = dict(raw)
    if set(payload) - _COMPATIBILITY_FIELDS:
        raise ValueError("vector_index_compatibility_fields_forbidden")
    dimensions = payload.get("dimensions")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ValueError("vector_index_compatibility_invalid")
    for field in _COMPATIBILITY_TEXT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("vector_index_compatibility_incomplete")
    try:
        compatibility = CompatibilitySpec(
            dimensions=dimensions,
            distance=payload["distance"],
            provider=payload["provider"],
            model=payload["model"],
            profile=payload["profile"],
            encoding=payload["encoding"],
            config_hash=payload["config_hash"],
            schema_version=payload["schema_version"],
            manifest_hash=payload["manifest_hash"],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("vector_index_compatibility_invalid") from exc
    return compatibility.as_dict()


@dataclass(frozen=True)
class VectorIndexTrustedScope:
    """Trusted Hub scope used to serialize one vector-index mutation stream."""

    workspace_id: str
    repository_id: str
    profile_name: str = "default"
    domain: str = "codecompass"

    def __post_init__(self) -> None:
        workspace = _scope_value(self.workspace_id, field="workspace_id")
        repository = _scope_value(self.repository_id, field="repository_id")
        profile = _scope_value(self.profile_name, field="profile_name")
        domain = str(self.domain or "").strip().lower()
        if domain not in {"codecompass", "wiki"}:
            raise ValueError("vector_index_domain_invalid")
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "repository_id", repository)
        object.__setattr__(self, "profile_name", profile)
        object.__setattr__(self, "domain", domain)

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "repository_id": self.repository_id,
            "profile_name": self.profile_name,
            "domain": self.domain,
        }

    def fingerprint(self) -> str:
        return VectorIndexArtifactLocator.scope_fingerprint(self)


@dataclass(frozen=True)
class VectorIndexMigrationPayload:
    """Validated migration-specific task payload."""

    dry_run: bool = False
    checkpoint: dict[str, Any] | None = None
    max_batches: int | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> "VectorIndexMigrationPayload":
        payload = dict(raw or {})
        if set(payload) - {"dry_run", "checkpoint", "max_batches"}:
            raise ValueError("vector_index_migration_fields_forbidden")
        if "dry_run" in payload and not isinstance(payload["dry_run"], bool):
            raise ValueError("vector_index_migration_dry_run_invalid")
        checkpoint = payload.get("checkpoint")
        if checkpoint is not None and not isinstance(checkpoint, Mapping):
            raise ValueError("vector_index_migration_checkpoint_invalid")
        normalized_checkpoint = cls._checkpoint(dict(checkpoint)) if isinstance(checkpoint, Mapping) else None
        maximum = payload.get("max_batches")
        if maximum is not None:
            if isinstance(maximum, bool):
                raise ValueError("vector_index_migration_max_batches_invalid")
            maximum = int(maximum)
            if not 1 <= maximum <= 100_000:
                raise ValueError("vector_index_migration_max_batches_invalid")
        return cls(
            dry_run=bool(payload.get("dry_run", False)),
            checkpoint=normalized_checkpoint,
            max_batches=maximum,
        )

    @staticmethod
    def _checkpoint(raw: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(raw or {})
        allowed = {
            "source_digest",
            "collection_name",
            "next_offset",
            "scope_fingerprint",
            "idempotency_key_hash",
        }
        if set(payload) - allowed:
            raise ValueError("vector_index_migration_checkpoint_fields_forbidden")
        source_digest = str(payload.get("source_digest") or "").strip().lower()
        scope_fingerprint = str(payload.get("scope_fingerprint") or "").strip().lower()
        idempotency_hash = str(payload.get("idempotency_key_hash") or "").strip().lower()
        collection_name = str(payload.get("collection_name") or "").strip()
        try:
            next_offset = int(payload.get("next_offset"))
        except (TypeError, ValueError) as exc:
            raise ValueError("vector_index_migration_checkpoint_invalid") from exc
        if (
            _SHA256.fullmatch(source_digest) is None
            or _SHA256.fullmatch(scope_fingerprint) is None
            or _SHA256.fullmatch(idempotency_hash) is None
            or not collection_name
            or len(collection_name) > 255
            or any(ord(character) < 32 for character in collection_name)
            or next_offset < 0
        ):
            raise ValueError("vector_index_migration_checkpoint_invalid")
        return {
            "source_digest": source_digest,
            "collection_name": collection_name,
            "next_offset": next_offset,
            "scope_fingerprint": scope_fingerprint,
            "idempotency_key_hash": idempotency_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"dry_run": self.dry_run}
        if self.checkpoint is not None:
            result["checkpoint"] = clone_json(self.checkpoint)
        if self.max_batches is not None:
            result["max_batches"] = self.max_batches
        return result


@dataclass(frozen=True)
class VectorIndexOperationPayload:
    """Validated, immutable payload for one vector-index operation."""

    points: tuple[dict[str, Any], ...] = ()
    point_ids: tuple[str, ...] = ()
    input_ref: dict[str, Any] | None = None
    preparation: dict[str, Any] | None = None
    compatibility: dict[str, Any] | None = None
    migration: dict[str, Any] | None = None
    batch_size: int = 128
    delete_all_scope: bool = False

    @classmethod
    def from_mapping(
        cls,
        operation: str,
        raw: Mapping[str, Any] | None,
    ) -> "VectorIndexOperationPayload":
        payload = dict(raw or {})
        if set(payload) - _PAYLOAD_FIELDS:
            raise ValueError("vector_index_payload_fields_forbidden")
        if _contains_plaintext_secret(payload):
            raise ValueError("vector_index_plaintext_secret_forbidden")
        points_raw = payload.get("points") or []
        if not isinstance(points_raw, list) or any(not isinstance(item, Mapping) for item in points_raw):
            raise ValueError("vector_index_points_invalid")
        if len(points_raw) > 1000:
            raise ValueError("vector_index_inline_points_limit_exceeded")
        point_ids_raw = payload.get("point_ids") or []
        if not isinstance(point_ids_raw, list):
            raise ValueError("vector_index_point_ids_invalid")
        point_ids = tuple(_scope_value(str(item), field="point_id") for item in point_ids_raw)
        input_ref = payload.get("input_ref")
        preparation = payload.get("preparation")
        compatibility = payload.get("compatibility")
        migration = payload.get("migration")
        for field, value in (
            ("input_ref", input_ref),
            ("preparation", preparation),
            ("compatibility", compatibility),
            ("migration", migration),
        ):
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"vector_index_{field}_invalid")
        normalized_input_ref = None
        if isinstance(input_ref, Mapping):
            try:
                normalized_input_ref = VectorIndexInputReference.from_mapping(
                    input_ref,
                    require_sha256=True,
                    require_scope_fingerprint=True,
                )
            except VectorIndexInputError as exc:
                raise ValueError(exc.reason) from exc
        preparation_spec = None
        normalized_preparation = None
        if isinstance(preparation, Mapping):
            preparation_spec = VectorIndexPreparationSpec.from_mapping(preparation)
            normalized_preparation = preparation_spec.to_dict()
        normalized_migration = None
        if isinstance(migration, Mapping):
            normalized_migration = VectorIndexMigrationPayload.from_mapping(migration)
        raw_batch_size = payload.get("batch_size", 128)
        if isinstance(raw_batch_size, bool):
            raise ValueError("vector_index_batch_size_invalid")
        try:
            batch_size = int(raw_batch_size or 128)
        except (TypeError, ValueError) as exc:
            raise ValueError("vector_index_batch_size_invalid") from exc
        if batch_size < 1 or batch_size > 1000:
            raise ValueError("vector_index_batch_size_invalid")
        normalized_operation = str(operation or "").strip().lower()
        if normalized_preparation is not None:
            if normalized_operation not in {
                "index",
                "refresh",
                "rebuild",
            }:
                raise ValueError("vector_index_preparation_operation_invalid")
            if normalized_input_ref is None:
                raise ValueError("vector_index_preparation_input_ref_required")
            if points_raw:
                raise ValueError("vector_index_preparation_input_ambiguous")
        elif points_raw and normalized_input_ref is not None:
            raise ValueError("vector_index_input_ambiguous")
        has_input = bool(points_raw) or normalized_input_ref is not None
        normalized_compatibility = _complete_compatibility(
            compatibility if isinstance(compatibility, Mapping) else None,
            required=(
                normalized_operation
                in {
                    "refresh",
                    "rebuild",
                    "migrate",
                }
                or normalized_preparation is not None
            ),
        )
        if preparation_spec is not None:
            preparation_spec.validate_compatibility(CompatibilitySpec(**dict(normalized_compatibility or {})))
        raw_delete_all_scope = payload.get("delete_all_scope", False)
        if not isinstance(raw_delete_all_scope, bool):
            raise ValueError("vector_index_delete_all_scope_invalid")
        delete_all_scope = raw_delete_all_scope
        if normalized_operation in {"index", "refresh", "rebuild"} and not has_input:
            raise ValueError("vector_index_input_required")
        if normalized_operation == "delete" and not (point_ids or delete_all_scope):
            raise ValueError("vector_index_delete_selector_required")
        if normalized_operation == "delete" and point_ids and delete_all_scope:
            raise ValueError("vector_index_delete_selector_ambiguous")
        if normalized_operation == "migrate":
            if normalized_migration is None:
                raise ValueError("vector_index_migration_contract_required")
            if normalized_input_ref is None:
                raise ValueError("vector_index_migration_source_required")
        return cls(
            points=tuple(clone_json(dict(item)) for item in points_raw),
            point_ids=point_ids,
            input_ref=(normalized_input_ref.to_dict() if normalized_input_ref is not None else None),
            preparation=normalized_preparation,
            compatibility=normalized_compatibility,
            migration=(normalized_migration.to_dict() if normalized_migration is not None else None),
            batch_size=batch_size,
            delete_all_scope=delete_all_scope,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"batch_size": self.batch_size}
        if self.points:
            result["points"] = [clone_json(item) for item in self.points]
        if self.point_ids:
            result["point_ids"] = list(self.point_ids)
        if self.input_ref is not None:
            result["input_ref"] = clone_json(self.input_ref)
        if self.preparation is not None:
            result["preparation"] = clone_json(self.preparation)
        if self.compatibility is not None:
            result["compatibility"] = clone_json(self.compatibility)
        if self.migration is not None:
            result["migration"] = clone_json(self.migration)
        if self.delete_all_scope:
            result["delete_all_scope"] = True
        return result


__all__ = [
    "VECTOR_INDEX_OPERATIONS",
    "VECTOR_INDEX_RESULT_SCHEMA",
    "VECTOR_INDEX_TASK_SCHEMA",
    "VectorIndexMigrationPayload",
    "VectorIndexOperationPayload",
    "VectorIndexTrustedScope",
    "canonical_json",
    "clone_json",
    "validate_idempotency_key",
]
