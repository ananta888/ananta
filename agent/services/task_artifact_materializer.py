from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


class TaskArtifactMaterializer:
    """Injects workspace_file artifacts from completed predecessor tasks into the current workspace.

    Download order:
    1. Local filesystem at storage_path (works when source and current task ran on same agent).
    2. HTTP GET {source_agent_url}/artifacts/{id}/content (cross-container via shared PostgreSQL metadata).
    3. HTTP GET {hub_url}/artifacts/{id}/content (fallback if artifact was uploaded to hub directly).
    """

    def materialize_predecessor_artifacts(
        self,
        *,
        goal_id: str,
        task_id: str,
        workspace_dir: Path,
        conflict_policy: str = "overwrite",
    ) -> list[dict]:
        """Fetch workspace_file artifacts from all completed sibling tasks and write them to workspace_dir.

        Returns a list of manifest entries describing what was materialized.
        """
        workspace_dir = Path(workspace_dir)
        try:
            from agent.repositories.tasks import TaskRepository
            all_tasks = TaskRepository().get_by_goal_id(goal_id)
        except Exception as exc:
            logging.warning("TaskArtifactMaterializer: could not load goal tasks: %s", exc)
            return []

        manifest: list[dict] = []
        for task in all_tasks:
            if str(task.id) == str(task_id):
                continue
            if str(task.status or "").strip().lower() != "completed":
                continue
            vs = dict(getattr(task, "verification_status", None) or {})
            refs = list(vs.get("execution_artifacts") or [])
            source_agent_url = str(task.assigned_agent_url or "").strip()
            for ref in refs:
                if ref.get("kind") != "workspace_file":
                    continue
                rel_path = str(ref.get("workspace_relative_path") or "").strip()
                artifact_id = str(ref.get("artifact_id") or "").strip()
                if not rel_path or not artifact_id:
                    continue
                dest = (workspace_dir / rel_path).resolve()
                if not str(dest).startswith(str(workspace_dir.resolve())):
                    logging.warning("TaskArtifactMaterializer: rejected path outside workspace: %s", rel_path)
                    continue
                if dest.exists() and conflict_policy == "keep_existing":
                    continue
                content = self._fetch_artifact(artifact_id, source_agent_url=source_agent_url)
                if content is None:
                    logging.warning("TaskArtifactMaterializer: could not fetch artifact %s", artifact_id)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                manifest.append({
                    "artifact_id": artifact_id,
                    "source_task_id": str(task.id),
                    "relative_path": rel_path,
                    "size_bytes": len(content),
                })
        if manifest:
            logging.info(
                "TaskArtifactMaterializer: materialized %d file(s) for task %s",
                len(manifest), task_id,
            )
        return manifest

    def _fetch_artifact(self, artifact_id: str, *, source_agent_url: str) -> Optional[bytes]:
        # 1. Try local storage_path (same-container fast path)
        content = self._try_local(artifact_id)
        if content is not None:
            return content
        # 2. Try source agent HTTP endpoint
        if source_agent_url:
            content = self._try_http(source_agent_url, artifact_id)
            if content is not None:
                return content
        # 3. Try hub
        try:
            from agent.config import settings
            hub = str(settings.hub_url or "").rstrip("/")
            if hub:
                content = self._try_http(hub, artifact_id)
                if content is not None:
                    return content
        except Exception:
            pass
        return None

    @staticmethod
    def _try_local(artifact_id: str) -> Optional[bytes]:
        try:
            from agent.repository import artifact_version_repo
            versions = artifact_version_repo.get_by_artifact(artifact_id)
            if versions:
                p = Path(versions[0].storage_path)
                if p.exists():
                    return p.read_bytes()
        except Exception:
            pass
        return None

    @staticmethod
    def _try_http(base_url: str, artifact_id: str) -> Optional[bytes]:
        try:
            import requests
            from agent.config import settings
            token = str(settings.agent_token or "").strip()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            url = f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content"
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.ok:
                return resp.content
        except Exception:
            pass
        return None


_instance: Optional[TaskArtifactMaterializer] = None


def get_task_artifact_materializer() -> TaskArtifactMaterializer:
    global _instance
    if _instance is None:
        _instance = TaskArtifactMaterializer()
    return _instance
