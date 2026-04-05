from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from flask import current_app

from agent.config import settings
from agent.services.ingestion_service import get_ingestion_service


def _safe_segment(value: str | None, *, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    return normalized or fallback


@dataclass(frozen=True)
class WorkerWorkspaceContext:
    workspace_dir: Path
    artifacts_dir: Path
    rag_helper_dir: Path
    artifact_sync: dict


class WorkerWorkspaceService:
    """Resolves per-agent task workspaces and syncs changed files as artifacts."""

    def resolve_workspace_context(self, *, task: dict) -> WorkerWorkspaceContext:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        workspace_cfg = dict(execution_context.get("workspace") or {})
        artifact_sync_cfg = dict(execution_context.get("artifact_sync") or {})

        runtime_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("worker_runtime")
        runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
        workspace_root = str(runtime_cfg.get("workspace_root") or "").strip()
        if not workspace_root:
            workspace_root = str(Path(settings.data_dir) / "worker-runtime")

        agent_name = _safe_segment(current_app.config.get("AGENT_NAME"), fallback="worker")
        task_id = _safe_segment(workspace_cfg.get("task_id") or task.get("id"), fallback="task")
        worker_job_id = _safe_segment(
            workspace_cfg.get("worker_job_id") or (task or {}).get("current_worker_job_id"),
            fallback="local",
        )
        scope_key = _safe_segment(workspace_cfg.get("scope_key"), fallback=f"{task_id}-{worker_job_id}")

        workspace_dir = Path(workspace_root) / agent_name / scope_key
        artifacts_dir = workspace_dir / "artifacts"
        rag_helper_dir = workspace_dir / "rag_helper"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        rag_helper_dir.mkdir(parents=True, exist_ok=True)

        artifact_sync = {
            "enabled": bool(artifact_sync_cfg.get("enabled", True)),
            "sync_to_hub": bool(artifact_sync_cfg.get("sync_to_hub", True)),
            "collection_name": str(artifact_sync_cfg.get("collection_name") or "task-execution-results").strip(),
            "max_changed_files": max(1, min(int(artifact_sync_cfg.get("max_changed_files") or 30), 200)),
            "max_file_size_bytes": max(1024, min(int(artifact_sync_cfg.get("max_file_size_bytes") or (2 * 1024 * 1024)), 25 * 1024 * 1024)),
        }
        return WorkerWorkspaceContext(
            workspace_dir=workspace_dir,
            artifacts_dir=artifacts_dir,
            rag_helper_dir=rag_helper_dir,
            artifact_sync=artifact_sync,
        )

    @staticmethod
    def snapshot_directory(workspace_dir: Path) -> dict[str, tuple[int, int]]:
        snapshot: dict[str, tuple[int, int]] = {}
        if not workspace_dir.exists():
            return snapshot
        for root, _, filenames in os.walk(workspace_dir):
            for name in filenames:
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rel = str(path.relative_to(workspace_dir))
                snapshot[rel] = (int(stat.st_mtime_ns), int(stat.st_size))
        return snapshot

    @staticmethod
    def detect_changed_files(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
        changed: set[str] = set()
        for rel, sig in after.items():
            if before.get(rel) != sig:
                changed.add(rel)
        for rel in before.keys():
            if rel not in after:
                changed.add(rel)
        return sorted(changed)

    def sync_changed_files_to_artifacts(
        self,
        *,
        task_id: str,
        task: dict,
        workspace_dir: Path,
        changed_rel_paths: list[str],
        sync_cfg: dict,
    ) -> list[dict]:
        if not sync_cfg.get("enabled") or not sync_cfg.get("sync_to_hub"):
            return []
        max_changed_files = int(sync_cfg.get("max_changed_files") or 30)
        max_file_size = int(sync_cfg.get("max_file_size_bytes") or (2 * 1024 * 1024))
        collection_name = str(sync_cfg.get("collection_name") or "task-execution-results").strip() or "task-execution-results"
        created_by = str((task or {}).get("assigned_agent_url") or current_app.config.get("AGENT_NAME") or "worker")

        refs: list[dict] = []
        ingestion = get_ingestion_service()
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
                }
            )
        return refs


worker_workspace_service = WorkerWorkspaceService()


def get_worker_workspace_service() -> WorkerWorkspaceService:
    return worker_workspace_service
