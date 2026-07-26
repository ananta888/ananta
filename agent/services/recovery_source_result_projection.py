"""Read projection for Hub-finalized Recovery source results."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_COUNT,
    MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES,
    MAX_RECOVERY_ARTIFACT_TOTAL_BYTES,
    RECOVERY_ARTIFACT_RECEIPT_FIELDS,
    RecoveryArtifactIngressContractError,
    validate_recovery_artifact_receipt_list,
)

RECOVERY_SOURCE_RESULT_SCHEMA = "ananta.recovery_source_result.v2"
_RECOVERY_POST_COMMIT_SCHEMA = (
    "ananta.recovery_source_post_commit.v1"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_POST_COMMIT_STATES = frozenset(
    {"pending", "processing", "completed"}
)
_VERIFIED_RECEIPT_REQUIRED_FIELDS = (
    RECOVERY_ARTIFACT_RECEIPT_FIELDS
    | frozenset(
        {
            "relative_path",
            "_exists",
            "_hash_verified",
            "required",
        }
    )
)
_VERIFIED_RECEIPT_OPTIONAL_FIELDS = frozenset({"id", "path"})


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_json(value: Any) -> bool:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        UnicodeError,
    ):
        return False
    return len(encoded) <= MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES


def _closed_receipt(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    row = dict(value)
    fields = set(row)
    if (
        not _VERIFIED_RECEIPT_REQUIRED_FIELDS.issubset(fields)
        or not fields.issubset(
            _VERIFIED_RECEIPT_REQUIRED_FIELDS
            | _VERIFIED_RECEIPT_OPTIONAL_FIELDS
        )
        or row.get("_exists") is not True
        or row.get("_hash_verified") is not True
        or row.get("required") is not True
        or row.get("relative_path")
        != row.get("workspace_relative_path")
        or (
            "id" in row
            and row.get("id") != row.get("artifact_id")
        )
        or (
            "path" in row
            and row.get("path")
            != row.get("workspace_relative_path")
        )
    ):
        return None
    return {
        field: row[field]
        for field in RECOVERY_ARTIFACT_RECEIPT_FIELDS
    }


def validate_recovery_source_artifact_aggregate(
    raw_artifacts: list[Any],
) -> list[dict[str, Any]] | None:
    """Validate the same global bounds used by callback projection."""

    if (
        len(raw_artifacts) > MAX_RECOVERY_ARTIFACT_COUNT
        or not _bounded_json(raw_artifacts)
    ):
        return None
    closed = [
        _closed_receipt(value)
        for value in raw_artifacts
    ]
    if any(value is None for value in closed):
        return None
    receipts = [
        dict(value)
        for value in closed
        if value is not None
    ]
    grouped: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = {}
    for receipt in receipts:
        provenance = _mapping(
            receipt.get("provenance_summary")
        )
        key = (
            str(receipt.get("task_id") or ""),
            str(provenance.get("manifest_digest") or ""),
        )
        grouped.setdefault(key, []).append(receipt)
    canonical_by_identity: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}
    try:
        for (task_id, _manifest_digest), values in grouped.items():
            canonical = validate_recovery_artifact_receipt_list(
                values,
                task_id=task_id,
            )
            for receipt in canonical:
                identity = (
                    str(receipt["artifact_id"]),
                    str(receipt["artifact_version_id"]),
                )
                if identity in canonical_by_identity:
                    return None
                canonical_by_identity[identity] = receipt
    except RecoveryArtifactIngressContractError:
        return None
    if (
        sum(int(value["size_bytes"]) for value in receipts)
        > MAX_RECOVERY_ARTIFACT_TOTAL_BYTES
    ):
        return None
    ordered: list[dict[str, Any]] = []
    for receipt in receipts:
        identity = (
            str(receipt.get("artifact_id") or ""),
            str(receipt.get("artifact_version_id") or ""),
        )
        canonical = canonical_by_identity.get(identity)
        if canonical is None:
            return None
        ordered.append(canonical)
    return ordered


def _project_artifact(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    artifact_id = str(receipt["artifact_id"])
    workspace_path = str(receipt["workspace_relative_path"])
    return {
        "kind": receipt["kind"],
        "task_id": receipt["task_id"],
        "artifact_id": artifact_id,
        "id": artifact_id,
        "artifact_version_id": receipt["artifact_version_id"],
        "filename": receipt["filename"],
        "media_type": receipt["media_type"],
        "workspace_relative_path": workspace_path,
        "relative_path": workspace_path,
        "path": workspace_path,
        "content_hash": receipt["content_hash"],
        "size_bytes": receipt["size_bytes"],
        "provenance_summary": dict(
            receipt["provenance_summary"]
        ),
        "_exists": True,
        "_hash_verified": True,
        "required": True,
    }


def project_recovery_source_callback_artifacts(
    task: Any,
) -> list[dict[str, Any]] | None:
    """Project Hub-owned aggregate artifacts for an outgoing callback.

    ``None`` means that this is not a finalized Recovery source and lets the
    caller use its legacy projection.  An empty list means that Recovery
    ownership was identified but its aggregate failed closed.
    """

    details = _mapping(_value(task, "status_reason_details"))
    marker = _mapping(details.get("recovery_source_post_commit"))
    if marker.get("schema") != _RECOVERY_POST_COMMIT_SCHEMA:
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        if recovery_task_role(task) == "source":
            return []
        return None
    verification = _mapping(_value(task, "verification_status"))
    result = _mapping(verification.get("model_recovery_result"))
    task_status = str(_value(task, "status") or "").strip().lower()
    expected_result_status = {
        "completed": "passed",
        "verification_failed": "failed",
    }.get(task_status)
    reason_code = str(result.get("reason_code") or "").strip()
    raw_artifacts = result.get("artifacts")
    artifact_count = result.get("artifact_count")
    if (
        expected_result_status is None
        or result.get("schema") != RECOVERY_SOURCE_RESULT_SCHEMA
        or result.get("status") != expected_result_status
        or not reason_code
        or reason_code
        != str(_value(task, "status_reason_code") or "").strip()
        or marker.get("state") not in _POST_COMMIT_STATES
        or marker.get("transition_status") != task_status
        or marker.get("transition_reason") != reason_code
        or _SHA256_PATTERN.fullmatch(
            str(marker.get("transition_id") or "")
        )
        is None
        or not isinstance(raw_artifacts, list)
        or not isinstance(artifact_count, int)
        or isinstance(artifact_count, bool)
        or artifact_count != len(raw_artifacts)
        or artifact_count > MAX_RECOVERY_ARTIFACT_COUNT
    ):
        return []
    receipts = validate_recovery_source_artifact_aggregate(
        raw_artifacts
    )
    if receipts is None:
        return []
    projected = [
        _project_artifact(value)
        for value in receipts
    ]
    return projected if _bounded_json(projected) else []


__all__ = [
    "RECOVERY_SOURCE_RESULT_SCHEMA",
    "project_recovery_source_callback_artifacts",
    "validate_recovery_source_artifact_aggregate",
]
