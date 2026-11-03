"""Worker workspace policy port: configuration to paths and guardrails.

Pure resolution of the workspace-related configuration slices so the
domain service never reaches into a framework config container itself
(DIP: callers inject the config source).
"""

from __future__ import annotations

from pathlib import Path


def resolve_output_dir_policy(agent_cfg: dict | None) -> dict:
    return dict((agent_cfg or {}).get("output_dir_policy") or {})


def resolve_workspace_root(agent_cfg: dict | None, *, default_root: str) -> str:
    runtime_cfg = (agent_cfg or {}).get("worker_runtime")
    runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    return str(runtime_cfg.get("workspace_root") or "").strip() or default_root


def resolve_workspace_reuse_mode(*, workspace_cfg: dict, agent_cfg: dict | None, effective_runtime_cfg: dict) -> str:
    runtime_cfg = (agent_cfg or {}).get("worker_runtime")
    runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    return (
        str(
            workspace_cfg.get("workspace_reuse_mode")
            or runtime_cfg.get("workspace_reuse_mode")
            or (effective_runtime_cfg or {}).get("workspace_reuse_mode")
            or ""
        )
        .strip()
        .lower()
    )


def default_data_workspace_root(data_dir: str) -> str:
    return str(Path(data_dir) / "worker-runtime")
