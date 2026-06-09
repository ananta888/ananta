from __future__ import annotations

import difflib
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

    @staticmethod
    def _truncate_text(value: str | None, *, limit: int | None) -> str:
        text = str(value or "")
        if not limit or limit <= 0:
            return text
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 14)].rstrip() + "\n\n[gekürzt]"

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

        # APRL-007: compose AGENTS.md from Root + active profile + runtime constraints
        from agent.services.agent_profile_service import get_agent_profile_service
        _profile_svc = get_agent_profile_service()
        _active_profile = _profile_svc.resolve_for_task(task)

        _runtime_constraints_lines = [
            "## Execution environment constraints",
            "- Do NOT use `sudo` — the execution environment is a Docker container without root privileges.",
            "- Do NOT use `su`, `sudo -i`, or any privilege escalation command.",
            "- Do NOT use `systemctl` — there is no systemd in this Docker container.",
            "- Do NOT use `service` — init.d service management is unavailable in this container.",
            "- Do NOT use `ss` — not installed. Use `netstat -tlnp` or `cat /proc/net/tcp` for port info.",
            "- To check if a process is running use `pgrep -x <name>` or `ps aux`.",
            "- To check open ports use `netstat -tlnp` or `cat /proc/net/tcp`.",
            "- Shell commands must work as a non-root user inside a container.",
            "- If the target software (nginx, apache, mysql, etc.) is not installed: write commands as a shell script file in the artifacts directory instead.",
            "",
            "## Workspace guidance",
            "- Read `.ananta/context-index.md` first for task-specific context files.",
            "- Read `.ananta/agent-profile.json` for the active agent profile metadata.",
            "- Use `rag_helper/` for retrieved research and knowledge files when present.",
        ]
        if include_response_contract:
            _runtime_constraints_lines.append("- Follow `.ananta/response-contract.md` for the required response format.")
        else:
            _runtime_constraints_lines.append("- Apply the requested changes directly in the workspace; results are collected from workspace diffs.")

        _composed_agents = _profile_svc.compose_content(
            _active_profile,
            runtime_constraints="\n".join(_runtime_constraints_lines),
        )

        agents_dst = workspace_dir / "AGENTS.md"
        self._write_text(agents_dst, _composed_agents)
        _record(agents_dst, key="agents_path")

        # APRL-008: write .ananta/agent-profile.json
        _profile_meta_path = bundle_dir / "agent-profile.json"
        self._write_json(_profile_meta_path, _active_profile.to_metadata())
        _record(_profile_meta_path, key="agent_profile_path")
        manifest["active_agent_profile"] = _active_profile.to_metadata()

        task_brief = bundle_dir / "task-brief.md"
        # APRL-009: include active agent profile in task-brief
        _profile_line = (
            f"- Active agent profile: {_active_profile.profile_id}"
            + (" (root-only fallback)" if _active_profile.is_fallback else "")
        )
        brief_assignment = self._truncate_text(str(base_prompt or "").strip(), limit=task_brief_char_limit).strip()
        task_lines = [
            "# Task Brief",
            "",
            f"- Task ID: {str(task.get('id') or '').strip() or 'unknown'}",
            f"- Title: {str(task.get('title') or '').strip() or 'unknown'}",
            f"- Execution mode: {'structured-json-proposal' if include_response_contract else 'interactive-workspace-execution'}",
            _profile_line,
            "",
            "## Current assignment (source of truth)",
            brief_assignment or "No task prompt available.",
        ]
        description = str(task.get("description") or "").strip()
        if description and description != str(base_prompt or "").strip():
            task_lines.extend(
                [
                    "",
                    "## Task metadata description (secondary context)",
                    self._truncate_text(description, limit=task_brief_char_limit).strip(),
                ]
            )
        task_lines.extend(
            [
                "",
                "## Working directives",
                "- Prioritize the current assignment above metadata if they differ.",
                "- Apply changes directly in this workspace and keep edits auditable.",
            ]
        )
        if include_response_contract:
            task_lines.append("- Return exactly one JSON object according to `.ananta/response-contract.md`.")
        else:
            task_lines.append("- No JSON response is required; workspace diffs are collected automatically after the run.")
        self._write_text(task_brief, "\n".join(task_lines).strip() + "\n")
        _record(task_brief, key="task_brief_path")

        response_contract = bundle_dir / "response-contract.md"
        if include_response_contract:
            if allow_complex_shell:
                shell_rule = (
                    "- `command` may use pipelines (`|`), redirects (`>`, `<`, `2>&1`), "
                    "and chaining (`&&`, `||`, `;`) — full shell syntax is allowed."
                )
            else:
                shell_rule = (
                    "- `command` must not use shell chaining or redirection (`&&`, `||`, `;`, `>`, `<`, `|`)."
                )
            response_lines = [
                "# Response Contract",
                "",
                "Return exactly one JSON object and no Markdown.",
                "",
                "Required rules:",
                "- The first character must be '{' and the last character must be '}'.",
                "- Set at least one of `command` or `tool_calls`.",
                "- `reason` must stay short and technical.",
                "- Prefer `tool_calls` for file, directory, and code-change operations.",
                "- If `command` is used, it must be exactly one concrete shell command.",
                shell_rule,
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
        elif response_contract.exists():
            response_contract.unlink(missing_ok=True)

        if system_prompt:
            system_prompt_path = bundle_dir / "system-prompt.md"
            self._write_text(system_prompt_path, str(system_prompt).strip() + "\n")
            _record(system_prompt_path, key="system_prompt_path")

        if context_text:
            hub_context_path = bundle_dir / "hub-context.md"
            self._write_text(
                hub_context_path,
                self._truncate_text(str(context_text).strip(), limit=context_text_char_limit).strip() + "\n",
            )
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
                self._write_text(
                    research_md_path,
                    self._truncate_text(prompt_section, limit=research_prompt_char_limit).strip() + "\n",
                )
                _record(research_md_path, key="research_context_prompt_path")

        if isinstance(pattern_hints, dict) and pattern_hints:
            pattern_dir = bundle_dir / "patterns"
            pattern_dir.mkdir(parents=True, exist_ok=True)
            contract_path = pattern_dir / "pattern-selection-contract.json"
            self._write_json(contract_path, {
                "schema": "pattern_selection_contract.v1",
                "allowed_patterns": list(pattern_hints.get("allowed_patterns") or []),
                "preferred_patterns": list(pattern_hints.get("preferred_patterns") or []),
                "forbid_patterns": list(pattern_hints.get("forbid_patterns") or []),
                "language_targets": list(pattern_hints.get("language_targets") or []),
                "require_tests": bool(pattern_hints.get("require_tests", True)),
            })
            _record(contract_path, key="pattern_selection_contract_path")
            allowed_md_lines = [
                "# Allowed Design Patterns",
                "",
                "Use ONLY the pattern IDs listed here when proposing a pattern_plan.",
                "Patterns not in this list will be rejected by the hub validator.",
                "",
            ]
            allowed = list(pattern_hints.get("allowed_patterns") or [])
            if allowed:
                allowed_md_lines.append("## Allowed")
                for pid in allowed:
                    allowed_md_lines.append(f"- `{pid}`")
                allowed_md_lines.append("")
            preferred = list(pattern_hints.get("preferred_patterns") or [])
            if preferred:
                allowed_md_lines.append("## Preferred (subset of allowed)")
                for pid in preferred:
                    allowed_md_lines.append(f"- `{pid}`")
                allowed_md_lines.append("")
            forbidden = list(pattern_hints.get("forbid_patterns") or [])
            if forbidden:
                allowed_md_lines.append("## Forbidden")
                for pid in forbidden:
                    allowed_md_lines.append(f"- `{pid}` — must NOT be used")
                allowed_md_lines.append("")
            if bool(pattern_hints.get("require_tests", True)):
                allowed_md_lines.append("**Tests are required for any pattern output.**")
            else:
                allowed_md_lines.append("*Tests are optional for this step.*")
            allowed_path = pattern_dir / "allowed-patterns.md"
            self._write_text(allowed_path, "\n".join(allowed_md_lines).strip() + "\n")
            _record(allowed_path, key="pattern_allowed_path")
            manifest["pattern_context_paths"] = [
                str(manifest.get("pattern_selection_contract_path") or ""),
                str(manifest.get("pattern_allowed_path") or ""),
            ]

        context_index = bundle_dir / "context-index.md"
        index_lines = [
            "# OpenCode Workspace Context",
            "",
            "Read these files before planning or executing changes:",
        ]
        preferred_keys = [
            "agents_path",
            "agent_profile_path",  # APRL-008: profile metadata second
            "task_brief_path",
            "system_prompt_path",
            "hub_context_path",
            "research_context_prompt_path",
            "research_context_json_path",
            "tool_definitions_path",
            "output_schema_path",
            "pattern_selection_contract_path",
            "pattern_allowed_path",
        ]
        if include_response_contract:
            preferred_keys.append("response_contract_path")
        for key in preferred_keys:
            rel = str(manifest.get(key) or "").strip()
            if rel:
                index_lines.append(f"- `{rel}`")
        self._write_text(context_index, "\n".join(index_lines).strip() + "\n")
        _record(context_index, key="context_index_path")
        return manifest

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
