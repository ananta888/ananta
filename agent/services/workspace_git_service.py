from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class WorkspaceGitInitError(RuntimeError):
    def __init__(self, message: str, workspace_dir: Path, stderr: str = "") -> None:
        super().__init__(message)
        self.workspace_dir = workspace_dir
        self.stderr = stderr


@dataclass(frozen=True)
class WorkspaceGitContext:
    workspace_dir: Path
    repo_root: Path
    branch: str
    remote_url: Optional[str]
    is_clone: bool


def _sanitize_segment(value: str, max_len: int = 12) -> str:
    raw = re.sub(r"[^a-z0-9-]", "", str(value).lower())
    return raw[:max_len].strip("-") or "workspace"


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        raise WorkspaceGitInitError(
            f"git {args[0]} failed: {exc}", workspace_dir=cwd
        ) from exc


class WorkspaceGitService:
    def init_workspace(
        self,
        workspace_dir: Path,
        *,
        remote_url: Optional[str],
        branch: str,
        enabled: bool = True,
    ) -> WorkspaceGitContext:
        workspace_dir = Path(workspace_dir)
        if not enabled:
            return WorkspaceGitContext(
                workspace_dir=workspace_dir,
                repo_root=workspace_dir,
                branch=branch,
                remote_url=remote_url,
                is_clone=False,
            )

        git_dir = workspace_dir / ".git"
        is_clone = bool(remote_url)

        if git_dir.exists():
            self._ensure_branch(workspace_dir, branch=branch)
            return WorkspaceGitContext(
                workspace_dir=workspace_dir,
                repo_root=workspace_dir,
                branch=branch,
                remote_url=remote_url,
                is_clone=is_clone,
            )

        workspace_dir.mkdir(parents=True, exist_ok=True)

        if remote_url:
            res = _run_git(
                ["clone", remote_url, str(workspace_dir), "--no-local"],
                cwd=workspace_dir.parent,
            )
            if res.returncode != 0:
                raise WorkspaceGitInitError(
                    f"git clone failed for {remote_url}",
                    workspace_dir=workspace_dir,
                    stderr=res.stderr,
                )
            self._ensure_branch(workspace_dir, branch=branch)
        else:
            res = _run_git(["init"], cwd=workspace_dir)
            if res.returncode != 0:
                raise WorkspaceGitInitError(
                    "git init failed",
                    workspace_dir=workspace_dir,
                    stderr=res.stderr,
                )
            res = _run_git(["checkout", "-b", branch], cwd=workspace_dir)
            if res.returncode != 0:
                res2 = _run_git(["checkout", branch], cwd=workspace_dir)
                if res2.returncode != 0:
                    raise WorkspaceGitInitError(
                        f"Failed to create/checkout branch '{branch}'",
                        workspace_dir=workspace_dir,
                        stderr=res2.stderr,
                    )

        return WorkspaceGitContext(
            workspace_dir=workspace_dir,
            repo_root=workspace_dir,
            branch=branch,
            remote_url=remote_url,
            is_clone=is_clone,
        )

    def _ensure_branch(self, workspace_dir: Path, *, branch: str) -> None:
        res = _run_git(["checkout", branch], cwd=workspace_dir)
        if res.returncode != 0:
            res2 = _run_git(["checkout", "-b", branch], cwd=workspace_dir)
            if res2.returncode != 0:
                raise WorkspaceGitInitError(
                    f"Failed to checkout branch '{branch}'",
                    workspace_dir=workspace_dir,
                    stderr=res2.stderr,
                )

    def resolve_branch_name(
        self,
        goal_id: str,
        worker_key: Optional[str],
        strategy: str,
    ) -> str:
        safe_goal = _sanitize_segment(str(goal_id or ""), max_len=12)
        if strategy == "goal_worker" and worker_key:
            safe_worker = _sanitize_segment(str(worker_key), max_len=20)
            name = f"goal/{safe_goal}/{safe_worker}"
        else:
            name = f"goal/{safe_goal}"
        return name[:80]


_instance: Optional[WorkspaceGitService] = None


def get_workspace_git_service() -> WorkspaceGitService:
    global _instance
    if _instance is None:
        _instance = WorkspaceGitService()
    return _instance
