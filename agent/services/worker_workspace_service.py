from __future__ import annotations

import json
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

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content or ""), encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _safe_rel(path: Path, root: Path) -> str:
        return str(path.relative_to(root)).replace("\\", "/")

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

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

    def prepare_opencode_context_files(
        self,
        *,
        task: dict,
        workspace_context: WorkerWorkspaceContext,
        base_prompt: str,
        system_prompt: str | None,
        context_text: str | None,
        expected_output_schema: dict | None,
        tool_definitions: list[dict] | None,
        research_context: dict | None,
    ) -> dict:
        workspace_dir = workspace_context.workspace_dir
        bundle_dir = workspace_dir / ".ananta"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, object] = {"workspace_dir": str(workspace_dir), "files": []}

        def _record(path: Path, *, key: str | None = None) -> str:
            rel = self._safe_rel(path, workspace_dir)
            files = manifest.setdefault("files", [])
            if isinstance(files, list) and rel not in files:
                files.append(rel)
            if key:
                manifest[key] = rel
            return rel

        agents_dst = workspace_dir / "AGENTS.md"
        agents_lines = [
            "# AGENTS.md",
            "",
            "This is a task-scoped OpenCode workspace for the Ananta project.",
            "",
            "## Mandatory architecture rules",
            "- The hub remains the control plane and owns orchestration, routing, policy, and the task queue.",
            "- Workers execute delegated work only.",
            "- Do not introduce worker-to-worker orchestration.",
            "- Preserve container boundaries and avoid implicit shared state.",
            "- Prefer additive, backward-compatible changes over breaking redesigns.",
            "",
            "## Engineering rules",
            "- Keep changes small, testable, and SOLID.",
            "- Reuse existing abstractions before adding new ones.",
            "- Keep behavior observable; do not hide failures.",
            "- Respect the task workspace as the primary place for new files and generated context.",
            "",
            "## Workspace guidance",
            "- Read `.ananta/context-index.md` first for task-specific context files.",
            "- Use `rag_helper/` for retrieved research and knowledge files when present.",
            "- Follow `.ananta/response-contract.md` for the required response format.",
        ]
        self._write_text(agents_dst, "\n".join(agents_lines).strip() + "\n")
        _record(agents_dst, key="agents_path")

        task_brief = bundle_dir / "task-brief.md"
        task_lines = [
            "# Task Brief",
            "",
            f"- Task ID: {str(task.get('id') or '').strip() or 'unknown'}",
            f"- Title: {str(task.get('title') or '').strip() or 'unknown'}",
            "",
            "## Current assignment",
            str(base_prompt or "").strip() or "No task prompt available.",
        ]
        description = str(task.get("description") or "").strip()
        if description and description != str(base_prompt or "").strip():
            task_lines.extend(["", "## Task description", description])
        self._write_text(task_brief, "\n".join(task_lines).strip() + "\n")
        _record(task_brief, key="task_brief_path")

        response_contract = bundle_dir / "response-contract.md"
        response_lines = [
            "# Response Contract",
            "",
            "Return exactly one JSON object and no Markdown.",
            "",
            "Required rules:",
            "- The first character must be '{' and the last character must be '}'.",
            "- Set at least one of `command` or `tool_calls`.",
            "- `reason` must stay short and technical.",
            "- If no tool call is needed, provide one concrete shell command in `command`.",
            "",
            "Expected shape:",
            "```json",
            '{',
            '  "reason": "Short technical reason",',
            '  "command": "optional shell command",',
            '  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "value" } } ]',
            '}',
            "```",
        ]
        self._write_text(response_contract, "\n".join(response_lines) + "\n")
        _record(response_contract, key="response_contract_path")

        if system_prompt:
            system_prompt_path = bundle_dir / "system-prompt.md"
            self._write_text(system_prompt_path, str(system_prompt).strip() + "\n")
            _record(system_prompt_path, key="system_prompt_path")

        if context_text:
            hub_context_path = bundle_dir / "hub-context.md"
            self._write_text(hub_context_path, str(context_text).strip() + "\n")
            _record(hub_context_path, key="hub_context_path")

        if expected_output_schema:
            schema_path = bundle_dir / "output-schema.json"
            self._write_json(schema_path, expected_output_schema)
            _record(schema_path, key="output_schema_path")

        if tool_definitions:
            tool_defs_path = bundle_dir / "tool-definitions.json"
            self._write_json(tool_defs_path, tool_definitions)
            _record(tool_defs_path, key="tool_definitions_path")

        if research_context:
            research_json_path = workspace_context.rag_helper_dir / "research-context.json"
            self._write_json(research_json_path, research_context)
            _record(research_json_path, key="research_context_json_path")
            prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
            if prompt_section:
                research_md_path = workspace_context.rag_helper_dir / "research-context.md"
                self._write_text(research_md_path, prompt_section + "\n")
                _record(research_md_path, key="research_context_prompt_path")

        context_index = bundle_dir / "context-index.md"
        index_lines = [
            "# OpenCode Workspace Context",
            "",
            "Read these files before planning or executing changes:",
        ]
        preferred_keys = [
            "agents_path",
            "task_brief_path",
            "system_prompt_path",
            "hub_context_path",
            "research_context_prompt_path",
            "research_context_json_path",
            "tool_definitions_path",
            "output_schema_path",
            "response_contract_path",
        ]
        for key in preferred_keys:
            rel = str(manifest.get(key) or "").strip()
            if rel:
                index_lines.append(f"- `{rel}`")
        self._write_text(context_index, "\n".join(index_lines).strip() + "\n")
        _record(context_index, key="context_index_path")
        return manifest

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
