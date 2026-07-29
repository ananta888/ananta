"""Pure validation helpers for Hub-owned Unsloth storage records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from agent.services.unsloth_storage_contracts import (
    StorageArtifactRecord,
    UnslothStorageError,
)

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def artifact_record(row: Sequence[Any]) -> StorageArtifactRecord:
    return StorageArtifactRecord(
        tenant_id=str(row[0]),
        owner_scope_digest=str(row[1]),
        artifact_id=str(row[2]),
        kind=str(row[3]),
        relative_ref=str(row[4]),
        job_id=str(row[5]),
        attempt_id=str(row[6]),
        sha256=str(row[7]),
        size_bytes=int(row[8]),
        created_at=float(row[9]),
        retention_until=float(row[10]),
        state=str(row[11]),
        cleanup_task_id=str(row[12]) if row[12] is not None else None,
    )


def scoped_relative_ref(
    *,
    tenant_id: str,
    owner_scope_digest: str,
    kind: str,
    relative_ref: str,
    job_id: str,
    attempt_id: str,
) -> str:
    raw = str(relative_ref or "")
    pure = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or "\\" in raw
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise UnslothStorageError(
            "storage_relative_ref_invalid",
            "Storage reference must be a contained relative path.",
            status_code=422,
        )
    parts = pure.parts
    if kind == "dataset":
        tenant_key = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        expected = ("tenants", tenant_key, "datasets")
        valid = len(parts) > len(expected) and parts[: len(expected)] == expected
    else:
        expected = (
            "tenants",
            owner_scope_digest,
            "jobs",
            job_id,
            "attempts",
            attempt_id,
        )
        suffix = parts[len(expected) :]
        valid = (
            len(parts) > len(expected)
            and parts[: len(expected)] == expected
            and (
                (kind == "workspace" and suffix[0] == "workspace")
                or (kind == "model" and suffix[0] == "model-cache")
                or (kind == "checkpoint" and suffix[0] == "checkpoints")
                or (kind == "export" and suffix[0] in {"adapter", "artifacts", "exports"})
            )
        )
    if not valid:
        raise UnslothStorageError(
            "storage_scope_binding_mismatch",
            "Storage path is not bound to its tenant, job, attempt, and kind.",
        )
    return pure.as_posix()


def tenant(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise UnslothStorageError(
            "storage_tenant_invalid",
            "A bounded tenant ID is required.",
            status_code=422,
        )
    return normalized


def opaque(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_ID.fullmatch(normalized) is None:
        raise UnslothStorageError(
            reason_code,
            "An opaque identifier is required.",
            status_code=422,
        )
    return normalized


def digest(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256.fullmatch(normalized) is None:
        raise UnslothStorageError(
            reason_code,
            "A lowercase SHA-256 digest is required.",
            status_code=422,
        )
    return normalized


def bounded_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise UnslothStorageError(
            "storage_size_invalid",
            "Storage size must be a bounded non-negative integer.",
            status_code=422,
        )
    return value


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
