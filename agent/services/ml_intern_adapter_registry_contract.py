"""Contracts and validation helpers for the ML-Intern adapter registry."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from agent.services.ml_intern_training_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)


class RegistryError(ValueError):
    """Fehler in der Adapter-Registry."""


class RegistryNotFoundError(RegistryError):
    """The adapter does not exist inside the caller's exact ownership scope."""


class RegistryVersionConflict(RegistryError):
    """A lifecycle mutation used a stale optimistic-lock version."""

    reason_code = "adapter_version_conflict"


class RegistryIdempotencyConflict(RegistryError):
    """A promotion idempotency key was reused for different evidence."""

    reason_code = "adapter_promotion_idempotency_conflict"


@dataclass
class AdapterRecord:
    adapter_id: str
    display_name: str
    version: str
    base_model: str
    method: str
    status: str
    created_at: str
    registry_version: int = 1
    tenant_id: str | None = None
    owner_subject: str | None = None
    artifact_paths: dict[str, str] = field(default_factory=dict)
    dataset_hash: str | None = None
    source_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    provenance_verified: bool = False
    config_hash: str | None = None
    artifact_sha256: str | None = None
    eval_report_ref: str | None = None
    eval_score: float | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    approval_reason: str | None = None
    rejected_reason: str | None = None
    task_kinds: list[str] = field(default_factory=list)
    updated_at: str | None = None
    notes: str | None = None
    promotion_history: list[dict[str, Any]] = field(default_factory=list)
    release_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in asdict(self).items()
            if v is not None
            or k in ("adapter_id", "display_name", "version", "base_model", "method", "status", "created_at")
        }


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _provenance_binding(
    *,
    dataset_hash: str | None,
    source_ids: list[str] | tuple[str, ...] | None,
    run_ids: list[str] | tuple[str, ...] | None,
    provenance_verified: bool,
) -> tuple[str | None, list[str], list[str]]:
    normalized_hash = str(dataset_hash or "").strip().lower() or None
    if normalized_hash is not None and not _is_sha256(normalized_hash):
        raise RegistryError("dataset_hash must be a lowercase SHA-256 digest")
    try:
        normalized_source_ids = list(normalize_source_ids(source_ids))
        normalized_run_ids = list(normalize_run_ids(run_ids))
    except MlInternTrainingContractError as exc:
        raise RegistryError(f"{exc.reason_code}: {exc}") from exc
    if provenance_verified and (normalized_hash is None or not normalized_source_ids or not normalized_run_ids):
        raise RegistryError("verified provenance requires dataset_hash plus provided source_ids and run_ids")
    return normalized_hash, normalized_source_ids, normalized_run_ids


def _promotion_evidence(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "dataset_hash",
        "source_ids",
        "run_ids",
        "metrics",
        "job_id",
        "attempt_id",
        "fencing_token_digest",
        "base_model_id",
        "base_model_sha256",
        "adapter_id",
        "adapter_sha256",
        "export_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RegistryError("promotion evidence contract is incomplete")
    for key in (
        "dataset_hash",
        "fencing_token_digest",
        "base_model_sha256",
        "adapter_sha256",
        "export_sha256",
    ):
        if not _is_sha256(value.get(key)):
            raise RegistryError(f"promotion evidence {key} is invalid")
    identifiers = {}
    for key in ("job_id", "attempt_id", "base_model_id", "adapter_id"):
        normalized = str(value.get(key) or "").strip()
        if not normalized or len(normalized) > 256:
            raise RegistryError(f"promotion evidence {key} is invalid")
        identifiers[key] = normalized
    try:
        source_ids = list(normalize_source_ids(value.get("source_ids")))
        run_ids = list(normalize_run_ids(value.get("run_ids")))
    except MlInternTrainingContractError as exc:
        raise RegistryError(f"{exc.reason_code}: {exc}") from exc
    if not source_ids or not run_ids:
        raise RegistryError("promotion evidence requires trusted source and run IDs")
    metrics = value.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise RegistryError("promotion evidence metrics are missing")
    try:
        encoded_metrics = json.dumps(
            metrics,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RegistryError("promotion evidence metrics are invalid") from exc
    if len(encoded_metrics.encode("utf-8")) > 32 * 1024:
        raise RegistryError("promotion evidence metrics exceed their bound")
    return {
        "dataset_hash": str(value["dataset_hash"]),
        "source_ids": source_ids,
        "run_ids": run_ids,
        "metrics": json.loads(encoded_metrics),
        **identifiers,
        "fencing_token_digest": str(value["fencing_token_digest"]),
        "base_model_sha256": str(value["base_model_sha256"]),
        "adapter_sha256": str(value["adapter_sha256"]),
        "export_sha256": str(value["export_sha256"]),
    }


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _scope_key(
    tenant_id: str | None,
    owner_subject: str | None,
) -> tuple[str | None, str | None]:
    tenant = _optional_text(tenant_id)
    owner = _optional_text(owner_subject)
    if (tenant is None) != (owner is None):
        raise RegistryError("tenant_id and owner_subject must be provided together")
    if tenant is not None and (len(tenant) > 192 or len(owner or "") > 192):
        raise RegistryError("adapter ownership scope exceeds its supported bounds")
    return tenant, owner


def _tenant_scope_digest(tenant_id: str, owner_subject: str) -> str:
    material = (f"ananta.ml-intern-training.scope.v1\x00{tenant_id}\x00{owner_subject}").encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _matches_scope(
    raw: dict[str, Any],
    scope: tuple[str | None, str | None],
) -> bool:
    return (_optional_text(raw.get("tenant_id")), _optional_text(raw.get("owner_subject"))) == scope


def _stored_version(raw: dict[str, Any]) -> int:
    value = raw.get("registry_version", 1)
    if isinstance(value, bool):
        return 1
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return min(max(1, parsed), 2_147_483_647)


def _bump_version(raw: dict[str, Any]) -> int:
    current = _stored_version(raw)
    if current >= 2_147_483_647:
        raise RegistryError("adapter registry version is exhausted")
    next_version = current + 1
    raw["registry_version"] = next_version
    return next_version


def _assert_expected_version(raw: dict[str, Any], expected_version: int | None) -> None:
    if expected_version is None:
        return
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise RegistryVersionConflict("expected_version must be a positive integer")
    actual = _stored_version(raw)
    if actual != expected_version:
        raise RegistryVersionConflict(f"stale adapter registry version: expected {expected_version}, current {actual}")


def _assert_record_expected_version(
    record: AdapterRecord,
    expected_version: int | None,
) -> None:
    _assert_expected_version({"registry_version": record.registry_version}, expected_version)
