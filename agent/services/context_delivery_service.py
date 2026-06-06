from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.services.worker_workspace_service import WorkerWorkspaceContext

log = logging.getLogger(__name__)


class ContextDeliveryError(RuntimeError):
    pass


@dataclass
class ContextDeliveryResult:
    delivered_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy_scope_mode: str = "full"
    codecompass_profile_used: Optional[str] = None
    selected_count: int = 0
    excluded_count: int = 0
    exclusion_reasons: dict[str, str] = field(default_factory=dict)
    # APRL-016: active agent profile metadata for this delivery
    active_agent_profile: Optional[dict] = None


class ContextDeliveryService:
    def deliver(
        self,
        task: dict,
        workspace_ctx: "WorkerWorkspaceContext",
    ) -> ContextDeliveryResult:
        from agent.services.workspace_context_policy import (
            WorkspaceContextPolicy,
            get_workspace_context_policy_resolver,
        )

        policy: WorkspaceContextPolicy = workspace_ctx.context_policy  # type: ignore[assignment]
        if policy is None:
            effective_config = dict((task or {}).get("effective_config") or {})
            task_kind = str((task or {}).get("task_kind") or "")
            agent_template = str((task or {}).get("agent_template") or "") or None
            policy = get_workspace_context_policy_resolver().resolve(effective_config, task_kind, agent_template)

        # APRL-016: resolve active agent profile for this task delivery
        try:
            from agent.services.agent_profile_service import get_agent_profile_service
            _active_profile = get_agent_profile_service().resolve_for_task(task)
            _active_profile_meta = _active_profile.to_metadata()
        except Exception:
            _active_profile_meta = None

        result = ContextDeliveryResult(
            policy_scope_mode=policy.scope_mode,
            active_agent_profile=_active_profile_meta,
        )
        llm_scope = self._resolve_llm_scope(task=task)
        effective_config = dict((task or {}).get("effective_config") or {})
        llm_config = dict(effective_config.get("llm_config") or {})
        provider = str(effective_config.get("default_provider") or llm_config.get("provider") or "").strip()
        base_url = str(llm_config.get("base_url") or "").strip()
        has_explicit_llm_target = bool(provider or base_url)
        workspace_policy_cfg = dict(effective_config.get("workspace_context_policy") or {})
        allow_full_for_cloud = bool(workspace_policy_cfg.get("allow_full_context_for_cloud", False))

        if policy.scope_mode == "full":
            if has_explicit_llm_target and llm_scope == "external_cloud_allowed" and not allow_full_for_cloud:
                result.policy_scope_mode = "none"
                result.warnings.append("full_context_blocked_for_external_cloud")
            return result

        if policy.scope_mode == "none":
            return result

        if policy.scope_mode == "selective":
            try:
                chunks = self._retrieve_chunks(task=task, policy=policy)
            except Exception as exc:
                raise ContextDeliveryError(
                    f"context_delivery_failed: retrieval error: {exc}"
                ) from exc

            from agent.services.context_file_selector import get_context_file_selector
            selection = get_context_file_selector().select(chunks, policy, llm_scope)
            result.selected_count = len(selection.selected_paths)
            result.excluded_count = len(selection.excluded_paths)
            result.exclusion_reasons = dict(selection.exclusion_reasons or {})

            result.codecompass_profile_used = policy.codecompass_profile

            repo_root = self._repo_root()
            for rel_path in selection.selected_paths:
                src = repo_root / rel_path
                dest = workspace_ctx.workspace_dir / rel_path
                if not src.exists():
                    result.skipped_paths.append(rel_path)
                    continue
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dest))
                    result.delivered_paths.append(rel_path)
                except Exception as exc:
                    result.skipped_paths.append(rel_path)
                    log.warning("Failed to copy %s: %s", rel_path, exc)

            if workspace_ctx.git_context is not None:
                self._git_add_delivered(
                    workspace_dir=workspace_ctx.workspace_dir,
                    delivered_paths=result.delivered_paths,
                    warnings=result.warnings,
                )

        return result

    def _retrieve_chunks(self, *, task: dict, policy) -> list[dict]:
        try:
            from agent.services.rag_helper_index_service import get_rag_helper_index_service
            svc = get_rag_helper_index_service()
            task_kind = str((task or {}).get("task_kind") or "")
            profile_name = policy.codecompass_profile or svc.select_profile(task_kind=task_kind)
            if not profile_name:
                return []
            query = str((task or {}).get("prompt") or (task or {}).get("description") or "")
            chunks = svc.retrieve(profile=profile_name, query=query, limit=policy.max_files * 2)
            return chunks if isinstance(chunks, list) else []
        except Exception:
            return []

    def _resolve_llm_scope(self, *, task: dict) -> str:
        try:
            from agent.services.context_file_selector import provider_to_llm_scope
            effective_config = dict((task or {}).get("effective_config") or {})
            workspace_policy_cfg = dict(effective_config.get("workspace_context_policy") or {})
            explicit_scope = str(
                workspace_policy_cfg.get("llm_scope")
                or effective_config.get("llm_scope")
                or ""
            ).strip().lower()
            if explicit_scope in {"local_only", "trusted_private_cloud", "external_cloud_allowed"}:
                return explicit_scope
            llm_config = dict(effective_config.get("llm_config") or {})
            provider = str(effective_config.get("default_provider") or llm_config.get("provider") or "")
            base_url = str(llm_config.get("base_url") or "")
            return provider_to_llm_scope(provider, base_url)
        except Exception:
            return "trusted_private_cloud"

    @staticmethod
    def _repo_root() -> Path:
        try:
            from agent.services.worker_workspace_service import WorkerWorkspaceService
            return WorkerWorkspaceService._repo_root()
        except Exception:
            return Path(".")

    def _git_add_delivered(
        self,
        *,
        workspace_dir: Path,
        delivered_paths: list[str],
        warnings: list[str],
    ) -> None:
        for rel_path in delivered_paths:
            try:
                res = subprocess.run(
                    ["git", "add", rel_path],
                    cwd=str(workspace_dir),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if res.returncode != 0:
                    warnings.append(f"git add failed for {rel_path}: {res.stderr.strip()}")
            except Exception as exc:
                warnings.append(f"git add error for {rel_path}: {exc}")


_instance: Optional[ContextDeliveryService] = None


def get_context_delivery_service() -> ContextDeliveryService:
    global _instance
    if _instance is None:
        _instance = ContextDeliveryService()
    return _instance
