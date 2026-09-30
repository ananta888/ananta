"""Artifact sync of changed worker workspace files.

Split out of ``worker_workspace_service`` (SRP): turning the files a worker
changed into Hub artifacts -- a unified workspace diff, per-file artifacts or,
for Recovery child tasks, lease-bound file claims without local Artifact rows.
``WorkerWorkspaceService`` keeps its public methods and delegates here; the
workspace-specific helpers (mutation-report filter, interactive baseline,
diff line reader) are consumed through the narrow ``WorkspaceDiffSource`` port.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable, Protocol

from flask import current_app

from agent.services.ingestion_service import get_ingestion_service


class WorkspaceDiffSource(Protocol):
    """The workspace helpers the artifact sync depends on."""

    def _mutation_sync_filter(
        self, *, workspace_dir: Path, changed_rel_paths: list[str]
    ) -> tuple[list[str], str | None]: ...

    def _interactive_terminal_baseline_dir(self, workspace_dir: Path) -> Path: ...

    def _read_text_lines_for_diff(self, path: Path) -> tuple[list[str] | None, str | None]: ...


class WorkspaceArtifactSync:
    """Sync changed workspace files to Hub artifacts (or Recovery claims)."""

    def __init__(
        self,
        *,
        workspace: WorkspaceDiffSource,
        ingestion_provider: Callable[[], Any] = get_ingestion_service,
    ) -> None:
        self._workspace = workspace
        self._ingestion_provider = ingestion_provider

    def create_workspace_diff_artifact(
        self,
        *,
        task_id: str,
        task: dict,
        workspace_dir: Path,
        changed_rel_paths: list[str],
        sync_cfg: dict,
    ) -> dict | None:
        if not sync_cfg.get("enabled") or not sync_cfg.get("sync_to_hub"):
            return None
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        if recovery_task_role(task) == "child":
            # Recovery output crosses the authoritative, lease-bound ingress
            # as real workspace files. A derived legacy DB artifact would be
            # unbound and duplicate the trusted Hub materialization.
            return None
        changed_rel_paths, sync_note = self._workspace._mutation_sync_filter(
            workspace_dir=workspace_dir, changed_rel_paths=changed_rel_paths
        )
        if sync_note == "mutation_policy_blocked":
            logging.warning("workspace diff artifact skipped: mutation policy blocked (task %s)", task_id)
            return None
        baseline_dir = self._workspace._interactive_terminal_baseline_dir(workspace_dir)
        if not baseline_dir.exists():
            return None
        diff_chunks: list[str] = []
        for rel in list(changed_rel_paths or []):
            before_path = baseline_dir / rel
            after_path = workspace_dir / rel
            before_lines, before_note = self._workspace._read_text_lines_for_diff(before_path)
            after_lines, after_note = self._workspace._read_text_lines_for_diff(after_path)
            if before_note or after_note:
                note = before_note or after_note or "diff unavailable"
                diff_chunks.append(f"diff --ananta {rel}\n# {note}\n")
                continue
            diff_text = "".join(
                difflib.unified_diff(
                    before_lines or [],
                    after_lines or [],
                    fromfile=f"a/{rel}",
                    tofile=f"b/{rel}",
                    lineterm="",
                )
            )
            if diff_text:
                diff_chunks.append(diff_text + "\n")
        diff_payload = "".join(diff_chunks).strip()
        if not diff_payload:
            return None
        collection_name = (
            str(sync_cfg.get("collection_name") or "task-execution-results").strip() or "task-execution-results"
        )
        created_by = str((task or {}).get("assigned_agent_url") or current_app.config.get("AGENT_NAME") or "worker")
        artifact, version, _ = self._ingestion_provider().upload_artifact(
            filename=f"{task_id or 'task'}-workspace.diff",
            content=diff_payload.encode("utf-8"),
            created_by=created_by,
            media_type="text/x-diff",
            collection_name=collection_name,
        )
        _, _, document = self._ingestion_provider().extract_artifact(artifact.id)
        return {
            "kind": "workspace_diff",
            "task_id": task_id,
            "worker_job_id": (task or {}).get("current_worker_job_id"),
            "artifact_id": artifact.id,
            "artifact_version_id": version.id,
            "extracted_document_id": document.id if document else None,
            "filename": artifact.latest_filename,
            "media_type": artifact.latest_media_type,
            "content_hash": version.sha256,
            "provenance_summary": {
                "artifact_type": "workspace_diff",
                "workspace_changed_files": len(list(changed_rel_paths or [])),
                "traceable_to_workspace": True,
            },
        }

    def collect_recovery_workspace_artifact_claims(
        self,
        *,
        task_id: str,
        task: dict,
        workspace_dir: Path,
        changed_rel_paths: list[str],
        sync_cfg: dict,
    ) -> list[dict]:
        """Describe Recovery files without creating local Artifact rows."""

        from agent.services.recovery_workspace_file_reader import (
            RecoveryWorkspaceFileReadError,
            get_recovery_workspace_file_reader,
        )
        from ananta_contracts.recovery_artifact_ingress import (
            MAX_RECOVERY_ARTIFACT_BYTES,
            MAX_RECOVERY_ARTIFACT_COUNT,
            MAX_RECOVERY_ARTIFACT_TOTAL_BYTES,
        )

        if not sync_cfg.get("enabled") or not sync_cfg.get("sync_to_hub"):
            return []
        changed_rel_paths, sync_note = self._workspace._mutation_sync_filter(
            workspace_dir=workspace_dir,
            changed_rel_paths=changed_rel_paths,
        )
        if sync_note == "mutation_policy_blocked":
            logging.warning(
                "recovery workspace claim skipped: mutation policy blocked (task %s)",
                task_id,
            )
            return []
        max_changed_files = min(
            MAX_RECOVERY_ARTIFACT_COUNT,
            max(
                0,
                int(sync_cfg.get("max_changed_files") or 30),
            ),
        )
        max_file_size = min(
            MAX_RECOVERY_ARTIFACT_BYTES,
            max(
                0,
                int(sync_cfg.get("max_file_size_bytes") or (2 * 1024 * 1024)),
            ),
        )
        refs: list[dict] = []
        total_bytes = 0
        reader = get_recovery_workspace_file_reader()
        for rel in changed_rel_paths[:max_changed_files]:
            try:
                snapshot = reader.read(
                    workspace_root=workspace_dir,
                    relative_path=str(rel),
                    maximum_bytes=max_file_size,
                )
            except RecoveryWorkspaceFileReadError:
                continue
            size_bytes = snapshot.size_bytes
            total_bytes += size_bytes
            if total_bytes > MAX_RECOVERY_ARTIFACT_TOTAL_BYTES:
                break
            refs.append(
                {
                    "kind": "workspace_file",
                    "task_id": task_id,
                    "worker_job_id": (task or {}).get("current_worker_job_id"),
                    "filename": snapshot.resolved_path.name,
                    "media_type": (mimetypes.guess_type(snapshot.resolved_path.name)[0] or "application/octet-stream"),
                    "workspace_relative_path": str(rel),
                    "content_hash": hashlib.sha256(snapshot.content).hexdigest(),
                    "size_bytes": size_bytes,
                    "provenance_summary": {
                        "artifact_type": "workspace_file_claim",
                        "authority": "executor_claim",
                        "persisted": False,
                    },
                }
            )
        return refs

    def sync_changed_files_to_artifacts(
        self,
        *,
        task_id: str,
        task: dict,
        workspace_dir: Path,
        changed_rel_paths: list[str],
        sync_cfg: dict,
    ) -> list[dict]:
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        if recovery_task_role(task) == "child":
            return self.collect_recovery_workspace_artifact_claims(
                task_id=task_id,
                task=task,
                workspace_dir=workspace_dir,
                changed_rel_paths=changed_rel_paths,
                sync_cfg=sync_cfg,
            )
        if not sync_cfg.get("enabled") or not sync_cfg.get("sync_to_hub"):
            return []
        changed_rel_paths, sync_note = self._workspace._mutation_sync_filter(
            workspace_dir=workspace_dir, changed_rel_paths=changed_rel_paths
        )
        if sync_note == "mutation_policy_blocked":
            logging.warning("workspace file sync skipped: mutation policy blocked (task %s)", task_id)
            return []
        max_changed_files = int(sync_cfg.get("max_changed_files") or 30)
        max_file_size = int(sync_cfg.get("max_file_size_bytes") or (2 * 1024 * 1024))
        collection_name = (
            str(sync_cfg.get("collection_name") or "task-execution-results").strip() or "task-execution-results"
        )
        created_by = str((task or {}).get("assigned_agent_url") or current_app.config.get("AGENT_NAME") or "worker")

        refs: list[dict] = []
        ingestion = self._ingestion_provider()
        for rel in changed_rel_paths[:max_changed_files]:
            absolute_path = (workspace_dir / rel).resolve()
            if not absolute_path.exists() or not absolute_path.is_file():
                continue
            try:
                if absolute_path.stat().st_size > max_file_size:
                    continue
                content = absolute_path.read_bytes()
            except OSError:
                continue
            artifact, version, _ = ingestion.upload_artifact(
                filename=absolute_path.name,
                content=content,
                created_by=created_by,
                collection_name=collection_name,
            )
            _, _, document = ingestion.extract_artifact(artifact.id)
            refs.append(
                {
                    "kind": "workspace_file",
                    "task_id": task_id,
                    "worker_job_id": (task or {}).get("current_worker_job_id"),
                    "artifact_id": artifact.id,
                    "artifact_version_id": version.id,
                    "extracted_document_id": document.id if document else None,
                    "filename": artifact.latest_filename,
                    "media_type": artifact.latest_media_type,
                    "workspace_relative_path": rel,
                    "content_hash": version.sha256,
                    "provenance_summary": {
                        "artifact_type": "workspace_file",
                        "workspace_relative_path": rel,
                        "traceable_to_workspace": True,
                    },
                }
            )
        return refs


__all__ = ["WorkspaceArtifactSync", "WorkspaceDiffSource"]
