"""Deterministic digests and names for browser-folder workspace snapshots.

Pure functions only: the idempotency key, published folder name, provisional
workspace id and plan digest must stay byte-for-byte stable across releases,
so they live apart from the I/O-heavy upload transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agent.services.source_control_workspace_snapshot_contracts import (
    BrowserFolderSnapshotRequest,
    StagedSnapshotManifest,
)
from agent.sources.git_source_connector_common import GitSourceScope

_FILE_TYPE = re.compile(r"[a-z0-9+_-]{1,32}")


def canonical_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def snapshot_file_type(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    return suffix if _FILE_TYPE.fullmatch(suffix) else "unknown"


def snapshot_operation_key(
    *,
    scope: GitSourceScope,
    idempotency_key: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"workspace-snapshot-v1\0{scope.tenant_id}\0"
            f"{scope.project_id}\0{scope.owner_id}\0{idempotency_key}"
        ).encode("utf-8")
    ).hexdigest()
    return f"workspace-snapshot:{digest}"


def published_snapshot_name(*, display_name: str, operation_key: str) -> str:
    suffix = hashlib.sha256(operation_key.encode("ascii")).hexdigest()[:16]
    return f"{display_name}--{suffix}"


def provisional_workspace_id(operation_key: str) -> str:
    token = hashlib.sha256(operation_key.encode("ascii")).hexdigest()
    return f"ws_{token}"


def snapshot_plan_digest(
    *,
    scope: GitSourceScope,
    request: BrowserFolderSnapshotRequest,
    manifest: StagedSnapshotManifest,
) -> str:
    return canonical_digest(
        {
            "operation": "workspace_snapshot_upload",
            "scope": {
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "owner_id": scope.owner_id,
            },
            "display_name": request.display_name,
            "manifest_digest": manifest.manifest_digest,
            "file_count": manifest.file_count,
            "total_bytes": manifest.total_bytes,
        }
    )


__all__ = [
    "canonical_digest",
    "provisional_workspace_id",
    "published_snapshot_name",
    "snapshot_file_type",
    "snapshot_operation_key",
    "snapshot_plan_digest",
]
