"""Worker-local, configuration-bound workspace resolution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class WorkerWorkspaceResolutionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkerWorkspaceContext:
    workspace_dir: Path


class ConfiguredWorkerWorkspaceResolver:
    """Resolve only paths inside the Worker container's configured mount."""

    def __init__(
        self,
        agent_config: Mapping[str, Any],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        runtime = agent_config.get("worker_runtime")
        runtime = runtime if isinstance(runtime, Mapping) else {}
        env = os.environ if environment is None else environment
        raw_root = str(
            runtime.get("workspace_root")
            or env.get("ANANTA_WORKSPACE_ROOT")
            or ""
        ).strip()
        root = Path(raw_root).expanduser()
        if not raw_root or not root.is_absolute() or "\x00" in raw_root:
            raise WorkerWorkspaceResolutionError(
                "worker_workspace_root_required"
            )
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise WorkerWorkspaceResolutionError(
                "worker_workspace_root_unavailable"
            ) from exc
        if not resolved.is_dir():
            raise WorkerWorkspaceResolutionError(
                "worker_workspace_root_unavailable"
            )
        self._root = resolved

    def resolve_workspace_context(
        self,
        *,
        task: Mapping[str, Any],
    ) -> WorkerWorkspaceContext:
        execution_context = task.get("worker_execution_context")
        execution_context = (
            execution_context if isinstance(execution_context, Mapping) else {}
        )
        workspace = execution_context.get("workspace")
        workspace = workspace if isinstance(workspace, Mapping) else {}
        raw_output = str(workspace.get("output_dir") or "").strip()
        if raw_output:
            candidate = self._output_candidate(raw_output)
        else:
            scope = self._safe_segment(
                workspace.get("scope_key")
                or workspace.get("task_id")
                or task.get("id"),
                fallback="task",
            )
            candidate = (self._root / scope).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise WorkerWorkspaceResolutionError(
                "worker_workspace_outside_configured_root"
            )
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            final = candidate.resolve(strict=True)
        except OSError as exc:
            raise WorkerWorkspaceResolutionError(
                "worker_workspace_unavailable"
            ) from exc
        if final != self._root and self._root not in final.parents:
            raise WorkerWorkspaceResolutionError(
                "worker_workspace_symlink_escape"
            )
        return WorkerWorkspaceContext(workspace_dir=final)

    def _output_candidate(self, raw_output: str) -> Path:
        requested = Path(raw_output).expanduser()
        if not requested.is_absolute():
            return (self._root / requested).resolve()
        candidate = requested.resolve()
        if candidate == self._root or self._root in candidate.parents:
            return candidate
        parts = requested.parts
        if "project-workspaces" in parts:
            index = parts.index("project-workspaces")
            relative = tuple(part for part in parts[index + 1 :] if part)
            return (self._root.joinpath(*relative)).resolve()
        return candidate

    @staticmethod
    def _safe_segment(value: object, *, fallback: str) -> str:
        normalized = re.sub(
            r"[^a-zA-Z0-9._-]+",
            "-",
            str(value or "").strip(),
        ).strip("-.")
        if not normalized:
            normalized = fallback
        return normalized[:128]


__all__ = [
    "ConfiguredWorkerWorkspaceResolver",
    "WorkerWorkspaceContext",
    "WorkerWorkspaceResolutionError",
]
