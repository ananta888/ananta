from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from flask import current_app, g as flask_g, has_app_context, has_request_context

from agent.config import settings
from agent.services.ingestion_service import get_ingestion_service
from agent.services.output_dir_lock_service import get_output_dir_lock_service


def _safe_segment(value: str | None, *, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-.")
    return normalized or fallback


@dataclass(frozen=True)
class WorkerWorkspaceContext:
    workspace_dir: Path
    artifacts_dir: Path
    rag_helper_dir: Path
    artifact_sync: dict
    git_context: object = None
    context_policy: object = None
    materialization_manifest: object = None  # list[dict] from TaskArtifactMaterializer, None if sync disabled


from agent.services._worker_workspace_context_writer import (
    prepare_opencode_context_files as _prepare_opencode_context_files,
    prepare_ananta_worker_context_files as _prepare_ananta_worker_context_files,
)


class WorkerWorkspaceService:
    """Resolves per-agent task workspaces and syncs changed files as artifacts."""

    _IGNORED_WORKSPACE_SEGMENTS = {".ananta", "artifacts", "__pycache__"}
    _DIFF_MAX_FILE_BYTES = 512 * 1024
    _NON_ARTIFACT_PATHS = {
        "AGENTS.md",
        "manifest.json",
        ".ananta/context-index.md",
        ".ananta/task-brief.md",
        ".ananta/response-contract.md",
        ".ananta/system-prompt.md",
        ".ananta/hub-context.md",
        "rag_helper/research-context.md",
        "rag_helper/research-context.json",
    }

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _is_within(child: Path, parent: Path) -> bool:
        try:
            return os.path.commonpath([str(child), str(parent)]) == str(parent)
        except Exception:
            return False

    def _resolve_workspace_dir(self, *, output_dir: str | None, workspace_root: str, agent_name: str, scope_key: str, explicit_scope_key: bool = False) -> Path:
        workspace_root_path = Path(os.path.abspath(str(Path(workspace_root).expanduser())))
        if output_dir:
            requested = self._normalize_requested_output_dir(
                output_dir=str(output_dir),
                workspace_root=workspace_root_path,
            )
            cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
            policy = dict(cfg.get("output_dir_policy") or {})
            unsafe_shared = bool(policy.get("unsafe_shared", False))
            if not unsafe_shared and not self._is_within(requested, workspace_root_path):
                if self._is_absolute_project_workspace_path(requested):
                    return requested
                remapped = self._try_remap_project_workspace_path(
                    requested=requested,
                    workspace_root=workspace_root_path,
                )
                if remapped is None:
                    logging.warning(
                        "Rejected workspace output_dir outside workspace_root",
                        extra={
                            "workspace_root": str(workspace_root_path),
                            "output_dir_requested": str(requested),
                        },
                    )
                    raise ValueError("workspace_output_dir_outside_workspace_root")
                requested = remapped
            return requested
        # When scope_key is explicitly set (goal_worker mode), omit agent_name so all
        # workers sharing the same bind-mounted workspace root use the same directory.
        if explicit_scope_key:
            return (workspace_root_path / scope_key).resolve()
        return (workspace_root_path / agent_name / scope_key).resolve()

    @staticmethod
    def _normalize_requested_output_dir(*, output_dir: str, workspace_root: Path) -> Path:
        raw = str(output_dir or "").strip()
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return Path(os.path.abspath(str(candidate)))
        rel_parts = list(candidate.parts)
        if "project-workspaces" in rel_parts:
            anchor_index = rel_parts.index("project-workspaces")
            rel_parts = [segment for segment in rel_parts[anchor_index + 1 :] if segment]
            if rel_parts:
                candidate = Path(*rel_parts)
            else:
                candidate = Path(".")
        return Path(os.path.abspath(str(workspace_root / candidate)))

    @staticmethod
    def _try_remap_project_workspace_path(*, requested: Path, workspace_root: Path) -> Path | None:
        """Map host-mirrored project workspace paths into the container workspace root.

        Example: /home/user/repo/project-workspaces/foo -> /project-workspaces/foo
        """
        parts = list(requested.parts)
        try:
            anchor_index = parts.index("project-workspaces")
        except ValueError:
            return None
        rel_parts = [segment for segment in parts[anchor_index + 1 :] if segment]
        remapped = (workspace_root / Path(*rel_parts)).resolve() if rel_parts else workspace_root
        try:
            if os.path.commonpath([str(remapped), str(workspace_root)]) != str(workspace_root):
                return None
        except Exception:
            return None
        return remapped

    @staticmethod
    def _is_absolute_project_workspace_path(path: Path) -> bool:
        normalized = Path(os.path.abspath(str(path)))
        parts = list(normalized.parts)
        return len(parts) >= 2 and parts[1] == "project-workspaces"

    @classmethod
    def _is_tracked_relative_path(cls, rel: str) -> bool:
        parts = [part for part in Path(str(rel or "")).parts if part]
        return not any(part in cls._IGNORED_WORKSPACE_SEGMENTS for part in parts)

    @classmethod
    def _iter_workspace_files(cls, root: Path, *, tracked_only: bool = True):
        if not root.exists():
            return
        for current_root, dirnames, filenames in os.walk(root):
            if tracked_only:
                dirnames[:] = [name for name in dirnames if name not in cls._IGNORED_WORKSPACE_SEGMENTS]
            current_root_path = Path(current_root)
            for name in filenames:
                path = current_root_path / name
                rel = str(path.relative_to(root)).replace("\\", "/")
                if tracked_only and not cls._is_tracked_relative_path(rel):
                    continue
                yield path, rel

    @staticmethod
    def _snapshot_tree(root: Path, *, tracked_only: bool) -> dict[str, tuple[int, int]]:
        snapshot: dict[str, tuple[int, int]] = {}
        if not root.exists():
            return snapshot
        for path, rel in WorkerWorkspaceService._iter_workspace_files(root, tracked_only=tracked_only):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[rel] = (int(stat.st_mtime_ns), int(stat.st_size))
        return snapshot

    @staticmethod
    def _interactive_terminal_baseline_dir(workspace_dir: Path) -> Path:
        return workspace_dir / ".ananta" / "interactive-baseline"

    @staticmethod
    def _read_text_lines_for_diff(path: Path) -> tuple[list[str] | None, str | None]:
        if not path.exists():
            return [], None
        try:
            payload = path.read_bytes()
        except OSError as exc:
            return None, f"unreadable file: {exc}"
        if len(payload) > WorkerWorkspaceService._DIFF_MAX_FILE_BYTES:
            return None, f"skipped large file ({len(payload)} bytes)"
        if b"\x00" in payload:
            return None, "binary file"
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = payload.decode("utf-8", errors="replace")
        return text.splitlines(keepends=True), None

    def resolve_workspace_context(self, *, task: dict) -> WorkerWorkspaceContext:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        workspace_cfg = dict(execution_context.get("workspace") or {})
        artifact_sync_cfg = dict(execution_context.get("artifact_sync") or {})

        agent_cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
        runtime_cfg = agent_cfg.get("worker_runtime")
        runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
        workspace_root = str(runtime_cfg.get("workspace_root") or "").strip()
        if not workspace_root:
            workspace_root = str(Path(settings.data_dir) / "worker-runtime")

        agent_name = _safe_segment(current_app.config.get("AGENT_NAME") if has_app_context() else settings.agent_name, fallback="worker")
        task_id = _safe_segment(workspace_cfg.get("task_id") or task.get("id"), fallback="task")
        worker_job_id = _safe_segment(
            workspace_cfg.get("worker_job_id") or (task or {}).get("current_worker_job_id"),
            fallback="local",
        )
        explicit_scope_key_str = str(workspace_cfg.get("scope_key") or "").strip()
        scope_key = _safe_segment(explicit_scope_key_str, fallback=f"{task_id}-{worker_job_id}")
        explicit_scope_key = bool(explicit_scope_key_str)
        effective_runtime_cfg = dict(((task or {}).get("effective_config") or {}).get("worker_runtime") or {})
        workspace_reuse_mode = str(
            workspace_cfg.get("workspace_reuse_mode")
            or runtime_cfg.get("workspace_reuse_mode")
            or effective_runtime_cfg.get("workspace_reuse_mode")
            or ""
        ).strip().lower()
        if workspace_reuse_mode == "goal_worker":
            goal_id_raw = _safe_segment((task or {}).get("goal_id"), fallback="")
            if goal_id_raw:
                scope_key = goal_id_raw
                explicit_scope_key = True

        output_dir = str(workspace_cfg.get("output_dir") or "").strip()
        workspace_dir = self._resolve_workspace_dir(
            output_dir=output_dir,
            workspace_root=workspace_root,
            agent_name=agent_name,
            scope_key=scope_key,
            explicit_scope_key=explicit_scope_key,
        )
        artifacts_dir = workspace_dir / "artifacts"
        rag_helper_dir = workspace_dir / "rag_helper"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        rag_helper_dir.mkdir(parents=True, exist_ok=True)

        if has_request_context():
            flask_g.workspace_dir = str(workspace_dir.resolve())

        artifact_sync = {
            "enabled": bool(artifact_sync_cfg.get("enabled", True)),
            "sync_to_hub": bool(artifact_sync_cfg.get("sync_to_hub", True)),
            "collection_name": str(artifact_sync_cfg.get("collection_name") or "task-execution-results").strip(),
            "max_changed_files": max(1, min(int(artifact_sync_cfg.get("max_changed_files") or 30), 200)),
            "max_file_size_bytes": max(1024, min(int(artifact_sync_cfg.get("max_file_size_bytes") or (2 * 1024 * 1024)), 25 * 1024 * 1024)),
        }

        git_context = self._init_git_context(task=task, workspace_dir=workspace_dir)
        if has_request_context() and git_context is not None:
            flask_g.git_context = git_context

        materialization_manifest = self._materialize_predecessor_artifacts(task=task, workspace_dir=workspace_dir)

        context_policy = self._resolve_context_policy(task=task)

        return WorkerWorkspaceContext(
            workspace_dir=workspace_dir,
            artifacts_dir=artifacts_dir,
            rag_helper_dir=rag_helper_dir,
            artifact_sync=artifact_sync,
            git_context=git_context,
            context_policy=context_policy,
            materialization_manifest=materialization_manifest,
        )

    def _init_git_context(self, *, task: dict, workspace_dir: Path):
        try:
            effective_config = dict((task or {}).get("effective_config") or {})
            git_workspace_cfg = dict((effective_config.get("git_workspace")) or {})
            if not git_workspace_cfg.get("enabled"):
                # Also check worker_execution_context.workspace.git_workspace (set per-goal by lifecycle_service)
                exec_ctx = dict((task or {}).get("worker_execution_context") or {})
                ws_git = dict((exec_ctx.get("workspace") or {}).get("git_workspace") or {})
                if not ws_git.get("enabled"):
                    return None
                git_workspace_cfg = ws_git
            from agent.services.workspace_git_service import get_workspace_git_service
            svc = get_workspace_git_service()
            goal_id = str((task or {}).get("goal_id") or "")
            worker_key = str((task or {}).get("worker_key") or (task or {}).get("agent_url") or "")
            branch_strategy = str(git_workspace_cfg.get("branch_strategy") or "goal")
            branch = svc.resolve_branch_name(goal_id, worker_key, branch_strategy)
            remote_url = git_workspace_cfg.get("remote_url") or None
            return svc.init_workspace(workspace_dir, remote_url=remote_url, branch=branch, enabled=True)
        except Exception:
            return None

    def _materialize_predecessor_artifacts(self, *, task: dict, workspace_dir: Path) -> list | None:
        """Inject workspace_file artifacts from completed sibling tasks when artifact_hub_sync is active.

        Returns the materialization manifest (list of injected file records) or None if sync is disabled.
        """
        try:
            execution_context = dict((task or {}).get("worker_execution_context") or {})
            effective_config = dict((task or {}).get("effective_config") or {})
            runtime_sync_mode = str((effective_config.get("worker_runtime") or {}).get("workspace_sync_mode") or "").strip().lower()
            agent_cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
            sync_mode = str(
                (execution_context.get("workspace") or {}).get("sync_mode")
                or agent_cfg.get("workspace", {}).get("sync_mode")
                or runtime_sync_mode
                or ""
            ).strip().lower()
            if sync_mode != "artifact_hub_sync":
                return None
            goal_id = str((task or {}).get("goal_id") or "").strip()
            task_id = str((task or {}).get("id") or "").strip()
            if not goal_id or not task_id:
                return None
            from agent.services.task_artifact_materializer import get_task_artifact_materializer
            return get_task_artifact_materializer().materialize_predecessor_artifacts(
                goal_id=goal_id,
                task_id=task_id,
                workspace_dir=workspace_dir,
            )
        except Exception as exc:
            logging.warning("Artifact materialization failed (non-fatal): %s", exc)
            return None

    def _resolve_context_policy(self, *, task: dict):
        try:
            from agent.services.workspace_context_policy import get_workspace_context_policy_resolver
            effective_config = dict((task or {}).get("effective_config") or {})
            task_kind = str((task or {}).get("task_kind") or "")
            agent_template = str((task or {}).get("agent_template") or "")
            return get_workspace_context_policy_resolver().resolve(
                effective_config, task_kind, agent_template or None
            )
        except Exception:
            from agent.services.workspace_context_policy import WorkspaceContextPolicy
            return WorkspaceContextPolicy()

    def acquire_output_dir_lock(self, *, task: dict, workspace_dir: Path) -> tuple[bool, str | None]:
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        enabled = bool(((cfg.get("workspace") or {}).get("output_dir_locking_enabled", True)))
        if not enabled:
            return True, None
        owner = str((task or {}).get("id") or "unknown-task").strip() or "unknown-task"
        ok, _lease, reason = get_output_dir_lock_service().acquire(
            output_dir=str(workspace_dir),
            owner=owner,
            ttl_seconds=int(((cfg.get("output_dir_policy") or {}).get("stale_lock_recovery_seconds") or 1800)),
        )
        if not ok:
            return False, "workspace_write_conflict"
        return True, reason

    def release_output_dir_lock(self, *, task: dict, workspace_dir: Path) -> None:
        owner = str((task or {}).get("id") or "unknown-task").strip() or None
        get_output_dir_lock_service().release(output_dir=str(workspace_dir), owner=owner)

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
        include_response_contract: bool = True,
        allow_complex_shell: bool = False,
        task_brief_char_limit: int | None = None,
        context_text_char_limit: int | None = None,
        research_prompt_char_limit: int | None = None,
        pattern_hints: dict | None = None,
    ) -> dict:
        return _prepare_opencode_context_files(
            task=task,
            workspace_context=workspace_context,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            context_text=context_text,
            expected_output_schema=expected_output_schema,
            tool_definitions=tool_definitions,
            research_context=research_context,
            include_response_contract=include_response_contract,
            allow_complex_shell=allow_complex_shell,
            task_brief_char_limit=task_brief_char_limit,
            context_text_char_limit=context_text_char_limit,
            research_prompt_char_limit=research_prompt_char_limit,
            pattern_hints=pattern_hints,
        )

    def prepare_ananta_worker_context_files(
        self,
        *,
        task: dict,
        workspace_context: WorkerWorkspaceContext,
        base_prompt: str,
        system_prompt: str | None = None,
        context_text: str | None = None,
        research_context: dict | None = None,
        mutation_mode: str = "read_only",
    ) -> dict:
        return _prepare_ananta_worker_context_files(
            task=task,
            workspace_context=workspace_context,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            context_text=context_text,
            research_context=research_context,
            mutation_mode=mutation_mode,
        )

    def refresh_mutation_baseline(self, *, workspace_dir: Path, mutation_mode: str = "controlled_workspace") -> dict:
        """AWWPI-006 / ALWA-013: refresh the baseline before any mutating
        execution. Emits a ``workspace_baseline_created`` audit event
        via ``audit_workspace_mutation_event`` with the baseline id /
        hash / workspace root / materialized file count.

        ``read_only`` mode does NOT emit a mutating-baseline event (it
        is skipped entirely per the track spec).
        """
        mode = str(mutation_mode or "").strip().lower()
        if mode == "read_only":
            return {"baseline_dir": None, "file_count": 0, "skipped": "read_only_mode"}
        try:
            meta = self.refresh_interactive_terminal_baseline(workspace_dir=workspace_dir)
        except OSError as exc:
            return {"baseline_dir": None, "file_count": 0, "warning": f"baseline_refresh_failed:{exc}"}

        # ALWA-013: audit the baseline creation. We hash the helper's
        # manifest.json (the only deterministic artifact of the
        # snapshot) so the audit row has a content-free reference to
        # the exact baseline state. If the manifest is missing we
        # still emit (id-only) so the audit row exists. We never
        # read file contents — the helper already redacts them.
        try:
            from agent.common.audit import (
                AUDIT_WORKSPACE_BASELINE_CREATED,
                audit_workspace_mutation_event,
            )
            manifest_path = Path(str(meta.get("baseline_dir") or "")) / "manifest.json"
            baseline_hash: str | None = None
            if manifest_path.is_file():
                baseline_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            file_count = int(meta.get("file_count") or 0)
            audit_workspace_mutation_event(
                AUDIT_WORKSPACE_BASELINE_CREATED,
                mutation_mode=mode,
                baseline_id=str(meta.get("baseline_dir") or "") or None,
                baseline_hash=baseline_hash,
                workspace_root_hash_or_id=str(workspace_dir),
                materialized_paths_count=file_count,
            )
        except Exception:
            # Audit must never fail the refresh.
            pass
        return meta

    def materialize_allowed_workspace_files(
        self,
        *,
        workspace_dir: Path,
        allowed_files: list[dict],
        source_root: Path | None = None,
    ) -> list[dict]:
        """AWWPI-007: copy hub-allowed files into the workspace with a manifest.

        Each entry of ``allowed_files`` is {"source": rel_path_in_repo,
        "allowed_operations": ["read"|"write"|"patch", ...]}. Files outside
        the source root or with traversal segments are skipped with a
        warning entry; the manifest records source, workspace_path, hash and
        allowed_operations and is persisted to
        ``.ananta/materialization-manifest.json``.
        """
        import hashlib

        root = (source_root or self._repo_root()).resolve()
        manifest: list[dict] = []
        for row in list(allowed_files or []):
            if not isinstance(row, dict):
                continue
            source_rel = str(row.get("source") or "").strip().replace("\\", "/")
            ops = [str(item or "").strip().lower() for item in list(row.get("allowed_operations") or ["read"])]
            if not source_rel or any(part == ".." for part in Path(source_rel).parts) or Path(source_rel).is_absolute():
                manifest.append({"source": source_rel, "skipped": "invalid_source_path"})
                continue
            source_path = (root / source_rel).resolve()
            if not self._is_within(source_path, root) or not source_path.is_file():
                manifest.append({"source": source_rel, "skipped": "outside_source_root_or_missing"})
                continue
            destination = workspace_dir / source_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest.append(
                {
                    "source": source_rel,
                    "workspace_path": source_rel,
                    "hash": digest,
                    "allowed_operations": ops,
                }
            )
        self._write_json(workspace_dir / ".ananta" / "materialization-manifest.json", manifest)
        return manifest

    @staticmethod
    def load_materialization_manifest(workspace_dir: Path) -> list[dict] | None:
        path = Path(workspace_dir) / ".ananta" / "materialization-manifest.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, list) else None

    def build_workspace_diff_text(
        self,
        *,
        workspace_dir: Path,
        changed_rel_paths: list[str],
        max_chars: int = 12000,
    ) -> tuple[str, bool]:
        """AWWPI-011: bounded unified diff against the baseline.

        Returns (diff_text, truncated). The full diff is still synced as a
        workspace_diff artifact by the regular sync path.
        """
        baseline_dir = self._interactive_terminal_baseline_dir(workspace_dir)
        if not baseline_dir.exists():
            return "", False
        chunks: list[str] = []
        for rel in list(changed_rel_paths or []):
            before_lines, before_note = self._read_text_lines_for_diff(baseline_dir / rel)
            after_lines, after_note = self._read_text_lines_for_diff(workspace_dir / rel)
            if before_note or after_note:
                chunks.append(f"diff --ananta {rel}\n# {before_note or after_note}\n")
                continue
            diff_text = "".join(
                difflib.unified_diff(
                    before_lines or [], after_lines or [], fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""
                )
            )
            if diff_text:
                chunks.append(diff_text + "\n")
        payload = "".join(chunks).strip()
        if max_chars > 0 and len(payload) > max_chars:
            return payload[: max(1, max_chars - 30)] + "\n…[diff truncated, see artifact]", True
        return payload, False

    @staticmethod
    def load_mutation_report(workspace_dir: Path) -> dict | None:
        path = Path(workspace_dir) / ".ananta" / "mutation-report.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _mutation_sync_filter(cls, *, workspace_dir: Path, changed_rel_paths: list[str]) -> tuple[list[str], str | None]:
        """AWWPI-017: drop blocked paths (or everything on policy_blocked).

        Without a mutation report the change list passes through unchanged
        so non-mutation workers keep their existing sync behavior.
        """
        report = cls.load_mutation_report(workspace_dir)
        if not report:
            return list(changed_rel_paths or []), None
        outcome = str(report.get("outcome") or "")
        policy = dict(report.get("final_policy_result") or {})
        if outcome == "policy_blocked" or str(policy.get("status") or "") == "policy_violation":
            blocked = {str(row.get("path") or "") for row in list(policy.get("blocked_changes") or [])}
            remaining = [rel for rel in list(changed_rel_paths or []) if rel not in blocked]
            if outcome == "policy_blocked":
                return [], "mutation_policy_blocked"
            return remaining, "mutation_blocked_paths_filtered"
        return list(changed_rel_paths or []), None

    @staticmethod
    def snapshot_directory(workspace_dir: Path) -> dict[str, tuple[int, int]]:
        return WorkerWorkspaceService._snapshot_tree(workspace_dir, tracked_only=True)

    def list_workspace_files(
        self,
        *,
        workspace_dir: Path,
        tracked_only: bool = True,
        max_entries: int = 2000,
    ) -> dict:
        safe_limit = max(1, min(int(max_entries or 2000), 10000))
        rows: list[dict] = []
        truncated = False

        for path, rel in self._iter_workspace_files(workspace_dir, tracked_only=tracked_only):
            if len(rows) >= safe_limit:
                truncated = True
                break
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append(
                {
                    "relative_path": rel,
                    "size_bytes": int(stat.st_size),
                    "modified_at": float(stat.st_mtime),
                }
            )

        rows.sort(key=lambda item: str(item.get("relative_path") or ""))
        return {
            "workspace_dir": str(workspace_dir),
            "tracked_only": bool(tracked_only),
            "max_entries": safe_limit,
            "file_count": len(rows),
            "truncated": truncated,
            "files": rows,
        }

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

    def refresh_interactive_terminal_baseline(self, *, workspace_dir: Path) -> dict:
        baseline_dir = self._interactive_terminal_baseline_dir(workspace_dir)
        if baseline_dir.exists():
            shutil.rmtree(baseline_dir, ignore_errors=True)
        baseline_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for source_path, rel in self._iter_workspace_files(workspace_dir, tracked_only=True):
            destination = baseline_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            copied.append(rel)
        self._write_json(
            baseline_dir / "manifest.json",
            {
                "created_at": time.time(),
                "workspace_dir": str(workspace_dir),
                "files": copied,
            },
        )
        return {
            "baseline_dir": str(baseline_dir),
            "file_count": len(copied),
        }

    def detect_changed_files_against_interactive_baseline(self, *, workspace_dir: Path) -> list[str]:
        baseline_dir = self._interactive_terminal_baseline_dir(workspace_dir)
        before = self._snapshot_tree(baseline_dir, tracked_only=False)
        after = self.snapshot_directory(workspace_dir)
        return self.detect_changed_files(before, after)

    @classmethod
    def filter_meaningful_changed_files(cls, changed_rel_paths: list[str] | None) -> list[str]:
        meaningful: list[str] = []
        for rel in list(changed_rel_paths or []):
            normalized = str(rel or "").strip().replace("\\", "/")
            if not normalized:
                continue
            if normalized in cls._NON_ARTIFACT_PATHS:
                continue
            if normalized.startswith(".ananta/"):
                continue
            if normalized.startswith("rag_helper/"):
                continue
            meaningful.append(normalized)
        return meaningful

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
        changed_rel_paths, sync_note = self._mutation_sync_filter(
            workspace_dir=workspace_dir, changed_rel_paths=changed_rel_paths
        )
        if sync_note == "mutation_policy_blocked":
            logging.warning("workspace diff artifact skipped: mutation policy blocked (task %s)", task_id)
            return None
        baseline_dir = self._interactive_terminal_baseline_dir(workspace_dir)
        if not baseline_dir.exists():
            return None
        diff_chunks: list[str] = []
        for rel in list(changed_rel_paths or []):
            before_path = baseline_dir / rel
            after_path = workspace_dir / rel
            before_lines, before_note = self._read_text_lines_for_diff(before_path)
            after_lines, after_note = self._read_text_lines_for_diff(after_path)
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
        collection_name = str(sync_cfg.get("collection_name") or "task-execution-results").strip() or "task-execution-results"
        created_by = str((task or {}).get("assigned_agent_url") or current_app.config.get("AGENT_NAME") or "worker")
        artifact, version, _ = get_ingestion_service().upload_artifact(
            filename=f"{task_id or 'task'}-workspace.diff",
            content=diff_payload.encode("utf-8"),
            created_by=created_by,
            media_type="text/x-diff",
            collection_name=collection_name,
        )
        _, _, document = get_ingestion_service().extract_artifact(artifact.id)
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
        changed_rel_paths, sync_note = self._mutation_sync_filter(
            workspace_dir=workspace_dir, changed_rel_paths=changed_rel_paths
        )
        if sync_note == "mutation_policy_blocked":
            logging.warning("workspace file sync skipped: mutation policy blocked (task %s)", task_id)
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
                    "content_hash": version.sha256,
                    "provenance_summary": {
                        "artifact_type": "workspace_file",
                        "workspace_relative_path": rel,
                        "traceable_to_workspace": True,
                    },
                }
            )
        return refs


worker_workspace_service = WorkerWorkspaceService()


def get_worker_workspace_service() -> WorkerWorkspaceService:
    return worker_workspace_service
