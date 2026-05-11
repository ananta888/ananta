"""FileChangeSet v1 — workspace before/after diff contracts."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileEntry:
    relative_path: str
    before_hash: str | None = None
    after_hash: str | None = None
    size_bytes: int | None = None
    classification: str | None = "internal"
    safe_preview_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "size_bytes": self.size_bytes,
            "classification": self.classification,
            "safe_preview_ref": self.safe_preview_ref,
        }


@dataclass
class FileChangeSet:
    task_id: str
    execution_id: str
    before_snapshot_id: str
    after_snapshot_id: str
    workspace_boundary_status: str = "ok"
    change_set_id: str = field(default_factory=lambda: f"fcs-{uuid.uuid4().hex[:12]}")
    created_files: list[FileEntry] = field(default_factory=list)
    modified_files: list[FileEntry] = field(default_factory=list)
    deleted_files: list[FileEntry] = field(default_factory=list)
    ignored_files: list[FileEntry] = field(default_factory=list)
    out_of_workspace_changes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "file_change_set.v1",
            "change_set_id": self.change_set_id,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "before_snapshot_id": self.before_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
            "workspace_boundary_status": self.workspace_boundary_status,
            "created_files": [e.to_dict() for e in self.created_files],
            "modified_files": [e.to_dict() for e in self.modified_files],
            "deleted_files": [e.to_dict() for e in self.deleted_files],
            "ignored_files": [e.to_dict() for e in self.ignored_files],
            "out_of_workspace_changes": list(self.out_of_workspace_changes),
        }


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_workspace(workspace_root: Path) -> dict[str, str]:
    """Return {relative_path: sha256} for all files in workspace."""
    snapshot: dict[str, str] = {}
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(workspace_root).as_posix()
        except ValueError:
            continue
        # Skip hidden ananta control dirs
        if rel.startswith(".ananta/"):
            continue
        try:
            snapshot[rel] = _hash_file(path)
        except OSError:
            pass
    return snapshot


def take_snapshot(workspace_root: Path) -> tuple[str, dict[str, str]]:
    """Take workspace snapshot. Returns (snapshot_id, {rel_path: sha256})."""
    snap = _snapshot_workspace(workspace_root)
    snap_id = hashlib.sha256(str(sorted(snap.items())).encode()).hexdigest()[:16]
    return snap_id, snap


def diff_snapshots(
    *,
    task_id: str,
    execution_id: str,
    workspace_root: Path,
    before_snapshot_id: str,
    before_snapshot: dict[str, str],
    after_snapshot_id: str,
    after_snapshot: dict[str, str],
) -> FileChangeSet:
    """Produce a FileChangeSet by comparing before/after snapshots."""
    all_paths = set(before_snapshot) | set(after_snapshot)
    ws_root = workspace_root.resolve()
    boundary_status = "ok"

    fcs = FileChangeSet(
        task_id=task_id,
        execution_id=execution_id,
        before_snapshot_id=before_snapshot_id,
        after_snapshot_id=after_snapshot_id,
    )

    for rel_path in sorted(all_paths):
        before_hash = before_snapshot.get(rel_path)
        after_hash = after_snapshot.get(rel_path)

        # Workspace boundary check
        try:
            resolved = (ws_root / rel_path).resolve()
            if not resolved.is_relative_to(ws_root):
                boundary_status = "violation"
                fcs.out_of_workspace_changes.append({"relative_path": rel_path, "reason": "escapes_workspace"})
                continue
        except (ValueError, OSError):
            boundary_status = "violation"
            fcs.out_of_workspace_changes.append({"relative_path": rel_path, "reason": "path_error"})
            continue

        abs_path = ws_root / rel_path
        size = abs_path.stat().st_size if abs_path.exists() else None

        entry = FileEntry(
            relative_path=rel_path,
            before_hash=before_hash,
            after_hash=after_hash,
            size_bytes=size,
        )

        if before_hash is None and after_hash is not None:
            fcs.created_files.append(entry)
        elif before_hash is not None and after_hash is None:
            fcs.deleted_files.append(entry)
        elif before_hash != after_hash:
            fcs.modified_files.append(entry)

    fcs.workspace_boundary_status = boundary_status
    return fcs
