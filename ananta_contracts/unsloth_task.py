"""Canonical serialization helpers for Hub-bound Unsloth task contracts."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


def canonical_unsloth_json(value: Mapping[str, Any]) -> str:
    """Serialize one closed task value identically in Hub and Worker processes."""

    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def unsloth_payload_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_unsloth_json(value).encode("utf-8")
    ).hexdigest()


UNSLOTH_STORAGE_CLEANUP_TASK_SCHEMA = (
    "ananta.unsloth-storage-cleanup-task.v1"
)
UNSLOTH_STORAGE_CLEANUP_RESULT_SCHEMA = (
    "ananta.unsloth-storage-cleanup-result.v1"
)
UNSLOTH_WORKER_TASK_RESULT_SCHEMA = (
    "ananta.unsloth-worker-task-result.v1"
)
UNSLOTH_STORAGE_CLEANUP_TASK_TYPE = "ml.storage.cleanup"
UNSLOTH_STORAGE_CLEANUP_CAPABILITY = "unsloth_storage_cleanup"
UNSLOTH_STORAGE_CLEANUP_RESULT_HANDLER = (
    "unsloth_storage_cleanup_v1"
)

_CLEANUP_PAYLOAD_FIELDS = frozenset(
    {
        "contract_version",
        "task_id",
        "tenant_scope_digest",
        "catalog_revision",
        "plan_sha256",
        "reason_sha256",
        "artifacts",
    }
)
_CLEANUP_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "kind",
        "relative_ref",
        "job_id",
        "attempt_id",
        "sha256",
        "size_bytes",
    }
)
_CLEANUP_RESULT_ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "kind", "status", "sha256"}
)
_CLEANUP_KINDS = frozenset({"workspace", "checkpoint", "export"})


def normalize_unsloth_cleanup_payload(
    value: object,
) -> dict[str, object]:
    """Validate and detach the closed Hub-to-Worker cleanup payload."""

    from collections.abc import Mapping
    from pathlib import PurePosixPath

    if not isinstance(value, Mapping) or set(value) != (
        _CLEANUP_PAYLOAD_FIELDS
    ):
        raise ValueError("cleanup_contract_invalid")
    if (
        value.get("contract_version")
        != UNSLOTH_STORAGE_CLEANUP_TASK_SCHEMA
    ):
        raise ValueError("cleanup_contract_invalid")
    task_id = _cleanup_identifier(
        value.get("task_id"),
        "cleanup_task_id_invalid",
    )
    tenant_scope_digest = _cleanup_digest(
        value.get("tenant_scope_digest"),
        "cleanup_scope_digest_invalid",
    )
    plan_sha256 = _cleanup_digest(
        value.get("plan_sha256"),
        "cleanup_plan_hash_invalid",
    )
    reason_sha256 = _cleanup_digest(
        value.get("reason_sha256"),
        "cleanup_reason_hash_invalid",
    )
    catalog_revision = value.get("catalog_revision")
    if (
        isinstance(catalog_revision, bool)
        or not isinstance(catalog_revision, int)
        or not 0 <= catalog_revision <= 2**63 - 1
    ):
        raise ValueError("cleanup_catalog_revision_invalid")
    raw_artifacts = value.get("artifacts")
    if (
        not isinstance(raw_artifacts, list)
        or not 1 <= len(raw_artifacts) <= 128
    ):
        raise ValueError("cleanup_artifact_selection_invalid")

    artifacts: list[dict[str, object]] = []
    artifact_ids: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, Mapping) or set(raw) != (
            _CLEANUP_ARTIFACT_FIELDS
        ):
            raise ValueError("cleanup_artifact_contract_invalid")
        artifact_id = _cleanup_identifier(
            raw.get("artifact_id"),
            "cleanup_artifact_id_invalid",
        )
        if artifact_id in artifact_ids:
            raise ValueError("cleanup_artifact_id_duplicate")
        artifact_ids.add(artifact_id)
        job_id = _cleanup_identifier(
            raw.get("job_id"),
            "cleanup_job_id_invalid",
        )
        attempt_id = _cleanup_identifier(
            raw.get("attempt_id"),
            "cleanup_attempt_id_invalid",
        )
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in _CLEANUP_KINDS:
            raise ValueError(
                "cleanup_kind_not_supported_by_training_worker"
            )
        sha256 = _cleanup_digest(
            raw.get("sha256"),
            "cleanup_artifact_hash_invalid",
        )
        size_bytes = raw.get("size_bytes")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 <= size_bytes <= 2**63 - 1
        ):
            raise ValueError("cleanup_artifact_size_invalid")
        relative_ref = str(raw.get("relative_ref") or "")
        relative = PurePosixPath(relative_ref)
        if (
            not relative_ref
            or "\x00" in relative_ref
            or "\\" in relative_ref
            or relative.is_absolute()
            or any(
                part in {"", ".", ".."}
                for part in relative.parts
            )
        ):
            raise ValueError("cleanup_path_invalid")
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "relative_ref": relative_ref,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "sha256": sha256,
                "size_bytes": size_bytes,
            }
        )
    return {
        "contract_version": (
            UNSLOTH_STORAGE_CLEANUP_TASK_SCHEMA
        ),
        "task_id": task_id,
        "tenant_scope_digest": tenant_scope_digest,
        "catalog_revision": catalog_revision,
        "plan_sha256": plan_sha256,
        "reason_sha256": reason_sha256,
        "artifacts": artifacts,
    }


def build_unsloth_cleanup_result(
    *,
    task_id: str,
    tenant_scope_digest: str,
    plan_sha256: str,
    artifacts: list[dict[str, object]],
    replayed: bool = False,
) -> dict[str, object]:
    """Build the closed inner cleanup result without exposing paths."""

    from collections.abc import Mapping

    normalized_artifacts: list[dict[str, str]] = []
    artifact_ids: set[str] = set()
    for raw in artifacts:
        if not isinstance(raw, Mapping) or set(raw) != (
            _CLEANUP_RESULT_ARTIFACT_FIELDS
        ):
            raise ValueError("cleanup_result_artifact_invalid")
        artifact_id = _cleanup_identifier(
            raw.get("artifact_id"),
            "cleanup_result_artifact_invalid",
        )
        if artifact_id in artifact_ids:
            raise ValueError("cleanup_result_artifact_invalid")
        artifact_ids.add(artifact_id)
        kind = str(raw.get("kind") or "").strip().lower()
        status = str(raw.get("status") or "").strip().lower()
        if (
            kind not in _CLEANUP_KINDS
            or status not in {"deleted", "already_absent"}
        ):
            raise ValueError("cleanup_result_artifact_invalid")
        normalized_artifacts.append(
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "status": status,
                "sha256": _cleanup_digest(
                    raw.get("sha256"),
                    "cleanup_result_artifact_invalid",
                ),
            }
        )
    if not isinstance(replayed, bool):
        raise ValueError("cleanup_result_replayed_invalid")
    return {
        "schema": UNSLOTH_STORAGE_CLEANUP_RESULT_SCHEMA,
        "task_id": _cleanup_identifier(
            task_id,
            "cleanup_task_id_invalid",
        ),
        "tenant_scope_digest": _cleanup_digest(
            tenant_scope_digest,
            "cleanup_scope_digest_invalid",
        ),
        "plan_sha256": _cleanup_digest(
            plan_sha256,
            "cleanup_plan_hash_invalid",
        ),
        "status": "completed",
        "deleted_count": sum(
            item["status"] == "deleted"
            for item in normalized_artifacts
        ),
        "artifacts": normalized_artifacts,
        "paths_exposed": False,
        "replayed": replayed,
    }


def build_unsloth_task_result(
    *,
    task_id: str,
    task_type: str,
    tenant_id: str,
    payload_sha256: str,
    status: str,
    result: object = None,
    reason_code: str | None = None,
    handler_contract: str | None = None,
) -> dict[str, object]:
    """Build the one closed Worker-to-Hub task result envelope."""

    from collections.abc import Mapping

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"completed", "failed"}:
        raise ValueError("unsloth_worker_result_status_invalid")
    if normalized_status == "completed":
        if reason_code is not None or not isinstance(result, Mapping):
            raise ValueError("unsloth_worker_result_invalid")
        normalized_result: dict[str, object] | None = dict(result)
        normalized_reason = None
    else:
        if result is not None:
            raise ValueError("unsloth_worker_result_invalid")
        normalized_result = None
        normalized_reason = _cleanup_identifier(
            reason_code,
            "unsloth_worker_result_reason_invalid",
        )
    envelope: dict[str, object] = {
        "schema": UNSLOTH_WORKER_TASK_RESULT_SCHEMA,
        "task_id": _cleanup_identifier(
            task_id,
            "unsloth_worker_result_task_id_invalid",
        ),
        "task_type": _bounded_task_text(
            task_type,
            "unsloth_worker_result_task_type_invalid",
        ),
        "tenant_id": _bounded_task_text(
            tenant_id,
            "unsloth_worker_result_tenant_invalid",
        ),
        "payload_sha256": _cleanup_digest(
            payload_sha256,
            "unsloth_worker_result_payload_hash_invalid",
        ),
        "status": normalized_status,
        "reason_code": normalized_reason,
        "result": normalized_result,
    }
    if handler_contract is not None:
        envelope["handler_contract"] = _cleanup_identifier(
            handler_contract,
            "unsloth_worker_result_handler_invalid",
        )
    return envelope


def _cleanup_identifier(value: object, reason_code: str) -> str:
    import re

    normalized = str(value or "").strip()
    if re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}",
        normalized,
    ) is None:
        raise ValueError(reason_code)
    return normalized


def _cleanup_digest(value: object, reason_code: str) -> str:
    import re

    normalized = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError(reason_code)
    return normalized


def _bounded_task_text(value: object, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 192
        or any(not character.isprintable() for character in normalized)
    ):
        raise ValueError(reason_code)
    return normalized


__all__ = [
    "UNSLOTH_STORAGE_CLEANUP_CAPABILITY",
    "UNSLOTH_STORAGE_CLEANUP_RESULT_HANDLER",
    "UNSLOTH_STORAGE_CLEANUP_RESULT_SCHEMA",
    "UNSLOTH_STORAGE_CLEANUP_TASK_SCHEMA",
    "UNSLOTH_STORAGE_CLEANUP_TASK_TYPE",
    "UNSLOTH_WORKER_TASK_RESULT_SCHEMA",
    "build_unsloth_cleanup_result",
    "build_unsloth_task_result",
    "canonical_unsloth_json",
    "normalize_unsloth_cleanup_payload",
    "unsloth_payload_sha256",
]
