"""WorkspaceStateSyncPolicy — normalises how workers exchange state via the hub."""
from __future__ import annotations

from dataclasses import dataclass

VALID_SYNC_MODES = frozenset({"artifact_hub_sync", "git_hub_remote", "shared_goal_workspace_dev", "none"})
VALID_CONFLICT_POLICIES = frozenset({"overwrite", "keep_existing", "fail"})

_SOURCE_OF_TRUTH = {
    "artifact_hub_sync": "hub_artifacts",
    "git_hub_remote": "hub_git_remote",
    "shared_goal_workspace_dev": "shared_filesystem",
    "none": "task_local",
}


@dataclass(frozen=True)
class WorkspaceStateSyncPolicy:
    sync_mode: str = "none"
    source_of_truth: str = "task_local"
    conflict_policy: str = "overwrite"
    is_unsafe_shared_fs: bool = False

    @classmethod
    def from_config(cls, config: dict) -> "WorkspaceStateSyncPolicy":
        """Build policy from a config dict (agent config or worker_execution_context.workspace)."""
        raw_mode = str((config or {}).get("sync_mode") or "none").strip().lower()
        sync_mode = raw_mode if raw_mode in VALID_SYNC_MODES else "none"
        conflict_raw = str((config or {}).get("conflict_policy") or "overwrite").strip().lower()
        conflict_policy = conflict_raw if conflict_raw in VALID_CONFLICT_POLICIES else "overwrite"
        source_of_truth = _SOURCE_OF_TRUTH.get(sync_mode, "task_local")
        is_unsafe = sync_mode == "shared_goal_workspace_dev"
        return cls(
            sync_mode=sync_mode,
            source_of_truth=source_of_truth,
            conflict_policy=conflict_policy,
            is_unsafe_shared_fs=is_unsafe,
        )

    @classmethod
    def resolve(cls, task: dict) -> "WorkspaceStateSyncPolicy":
        """Resolve the effective policy for a task from its execution context + global config."""
        try:
            execution_context = dict((task or {}).get("worker_execution_context") or {})
            ws_cfg = dict(execution_context.get("workspace") or {})
            if ws_cfg.get("sync_mode"):
                return cls.from_config(ws_cfg)
            from flask import current_app, has_app_context
            if has_app_context():
                agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
                global_ws = dict(agent_cfg.get("workspace") or {})
                if global_ws.get("sync_mode"):
                    return cls.from_config(global_ws)
        except Exception:
            pass
        return cls()

    def to_dict(self) -> dict:
        return {
            "sync_mode": self.sync_mode,
            "source_of_truth": self.source_of_truth,
            "conflict_policy": self.conflict_policy,
            "is_unsafe_shared_fs": self.is_unsafe_shared_fs,
        }
