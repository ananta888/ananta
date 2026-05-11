"""ArtifactManifest v1 — worker-side manifest builder and validator.

Workers write this manifest to .ananta/handoff/<execution_id>/artifact_manifest.v1.json
so the Hub can validate completion without parsing model chat output.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

_SAFE_PATH_RE = None


def _is_safe_relative_path(rel_path: str, workspace_root: Path) -> bool:
    """Return True only if rel_path resolves inside workspace_root."""
    try:
        resolved = (workspace_root / rel_path).resolve()
        return resolved.is_relative_to(workspace_root.resolve())
    except (ValueError, OSError):
        return False


def build_artifact_entry(
    *,
    workspace_root: Path,
    relative_path: str,
    kind: str = "generated_file",
    operation: str = "created",
    required: bool = False,
    classification: str | None = "internal",
    verification_status: str | None = "pending",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single artifact entry. Raises ValueError for unsafe paths."""
    if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
        raise ValueError(f"Unsafe artifact path rejected: {relative_path!r}")
    if not _is_safe_relative_path(relative_path, workspace_root):
        raise ValueError(f"Path escapes workspace: {relative_path!r}")
    abs_path = workspace_root / relative_path
    if not abs_path.exists():
        content_hash = "0" * 64
        size_bytes = 0
    else:
        content = abs_path.read_bytes()
        content_hash = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)
    valid_kinds = {
        "generated_file", "modified_file", "patch_file",
        "command_output", "test_result", "verification_result",
        "planner_proposal", "summary", "other",
    }
    if kind not in valid_kinds:
        kind = "other"
    return {
        "artifact_id": f"art-{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "relative_path": relative_path,
        "content_hash": content_hash,
        "size_bytes": size_bytes,
        "classification": classification,
        "operation": operation,
        "required": bool(required),
        "verification_status": verification_status,
        "metadata": metadata or {},
    }


def build_artifact_manifest(
    *,
    goal_id: str,
    task_id: str,
    execution_id: str,
    trace_id: str,
    workspace_root: Path,
    worker_id: str,
    artifacts: list[dict[str, Any]],
    summary: str | None = None,
    synthesized: bool = False,
) -> dict[str, Any]:
    """Build a complete ArtifactManifest v1 dict ready for serialization."""
    workspace_hash = hashlib.sha256(str(workspace_root.resolve()).encode()).hexdigest()[:16]
    return {
        "schema": "artifact_manifest.v1",
        "manifest_id": f"mfst-{uuid.uuid4().hex[:12]}",
        "goal_id": str(goal_id),
        "task_id": str(task_id),
        "execution_id": str(execution_id),
        "trace_id": str(trace_id),
        "workspace_root_ref": workspace_hash,
        "produced_by_worker_id": str(worker_id),
        "produced_at": time.time(),
        "summary": str(summary or "")[:2000] or None,
        "synthesized": bool(synthesized),
        "artifacts": list(artifacts),
    }


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write manifest JSON to output_path atomically."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output_path)


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and return raw manifest dict without schema validation."""
    return json.loads(path.read_text(encoding="utf-8"))
