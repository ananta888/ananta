"""WorkspaceDiffService — workspace snapshot + diff + synthesized manifest bridge."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from worker.core.artifact_manifest import build_artifact_manifest
from worker.core.file_change_set import FileChangeSet, diff_snapshots, take_snapshot

log = logging.getLogger(__name__)


class WorkspaceDiffService:
    def take_before_snapshot(self, workspace_root: Path) -> tuple[str, dict[str, str]]:
        """Take a before-execution snapshot of workspace files."""
        return take_snapshot(workspace_root)

    def take_after_snapshot(self, workspace_root: Path) -> tuple[str, dict[str, str]]:
        """Take an after-execution snapshot of workspace files."""
        return take_snapshot(workspace_root)

    def compute_diff(
        self,
        *,
        task_id: str,
        execution_id: str,
        workspace_root: Path,
        before_snapshot_id: str,
        before_snapshot: dict[str, str],
        after_snapshot_id: str,
        after_snapshot: dict[str, str],
    ) -> FileChangeSet:
        return diff_snapshots(
            task_id=task_id,
            execution_id=execution_id,
            workspace_root=workspace_root,
            before_snapshot_id=before_snapshot_id,
            before_snapshot=before_snapshot,
            after_snapshot_id=after_snapshot_id,
            after_snapshot=after_snapshot,
        )

    def synthesize_manifest(
        self,
        *,
        file_change_set: FileChangeSet,
        workspace_root: Path,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        worker_id: str = "hub-synthesized",
    ) -> dict[str, Any]:
        """Synthesize an artifact_manifest.v1 from a FileChangeSet.

        Marked synthesized=True and lower trust. Only usable when policy permits.
        """
        artifacts: list[dict[str, Any]] = []

        for entry in file_change_set.created_files + file_change_set.modified_files:
            rel_path = entry.relative_path
            # Safety check
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                log.warning("Workspace diff: unsafe path skipped: %r", rel_path)
                continue
            abs_path = workspace_root / rel_path
            if not abs_path.exists():
                continue
            content = abs_path.read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()
            operation = "created" if entry.before_hash is None else "modified"
            artifacts.append({
                "artifact_id": f"art-{uuid.uuid4().hex[:12]}",
                "kind": "generated_file" if operation == "created" else "modified_file",
                "relative_path": rel_path,
                "content_hash": content_hash,
                "size_bytes": len(content),
                "classification": "internal",
                "operation": operation,
                "required": False,
                "verification_status": "pending",
                "metadata": {"synthesized": True},
            })

        manifest = build_artifact_manifest(
            goal_id=goal_id,
            task_id=task_id,
            execution_id=execution_id,
            trace_id=trace_id,
            workspace_root=workspace_root,
            worker_id=worker_id,
            artifacts=artifacts,
            summary=f"Synthesized from workspace diff: {len(artifacts)} files",
            synthesized=True,
        )
        log.info(
            "WorkspaceDiffService: synthesized manifest for task %s with %d artifacts",
            task_id, len(artifacts),
        )
        return manifest


workspace_diff_service = WorkspaceDiffService()


def get_workspace_diff_service() -> WorkspaceDiffService:
    return workspace_diff_service
