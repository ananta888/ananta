from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _run_git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout or completed.stderr or "").strip() or f"git {' '.join(args)} failed")
    return (completed.stdout or "").strip()


@dataclass
class WorktreeSandbox:
    repository_root: Path
    worktree_path: Path
    base_ref: str

    def changed_files(self) -> list[str]:
        status_text = _run_git(["status", "--porcelain"], cwd=self.worktree_path)
        changed: list[str] = []
        for line in status_text.splitlines():
            if not line.strip():
                continue
            path = _status_path(line)
            if path and path not in changed:
                changed.append(path)
        return changed

    def cleanup(self) -> None:
        _run_git(["worktree", "remove", "--force", str(self.worktree_path)], cwd=self.repository_root)
        if self.worktree_path.exists():
            self.worktree_path.rmdir()


class WorktreeSandboxManager:
    def __init__(self, *, repository_root: Path, base_dir: Path | None = None) -> None:
        self.repository_root = repository_root.resolve()
        self.base_dir = base_dir
        if not (self.repository_root / ".git").exists():
            raise ValueError("repository_root_is_not_git_repo")

    def create(self, *, task_id: str, base_ref: str = "HEAD") -> WorktreeSandbox:
        task_token = str(task_id).strip() or "task"
        worktree_dir = Path(
            tempfile.mkdtemp(
                prefix=f"ananta-worker-{task_token}-",
                dir=str(self.base_dir) if self.base_dir is not None else None,
            )
        )
        _run_git(["worktree", "add", "--detach", str(worktree_dir), str(base_ref)], cwd=self.repository_root)
        return WorktreeSandbox(repository_root=self.repository_root, worktree_path=worktree_dir, base_ref=str(base_ref))


def _status_path(line: str) -> str:
    if len(line) >= 4 and line[2] == " ":
        return line[3:].strip()
    if len(line) >= 3:
        return line[2:].strip()
    return line.strip()
