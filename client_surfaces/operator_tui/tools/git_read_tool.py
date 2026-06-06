"""SCTR-005: read-only Git tool for SnakeChat."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GitReadResult:
    ok: bool
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}


class GitReadTool:
    """Small read-only wrapper around safe Git inspection commands."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(str(workspace_root or "")).resolve()

    def _run(self, args: list[str]) -> GitReadResult:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self._root,
                check=False,
                text=True,
                capture_output=True,
                timeout=5,
            )
        except FileNotFoundError:
            return GitReadResult(ok=False, error="git_not_found")
        except subprocess.TimeoutExpired:
            return GitReadResult(ok=False, error="git_timeout")
        if result.returncode != 0:
            stderr = str(result.stderr or "").strip()
            return GitReadResult(ok=False, error=f"git_error:{stderr[:200]}")
        return GitReadResult(ok=True, data=str(result.stdout or ""))

    def current_branch(self) -> GitReadResult:
        result = self._run(["branch", "--show-current"])
        if not result.ok:
            return result
        return GitReadResult(ok=True, data={"branch": str(result.data or "").strip()})

    def changed_files(self) -> GitReadResult:
        result = self._run(["status", "--porcelain=v1"])
        if not result.ok:
            return result
        files: list[dict[str, str]] = []
        for line in str(result.data or "").splitlines():
            if not line:
                continue
            status = line[:2]
            path = line[3:].strip()
            files.append({"path": path, "status": status.strip() or "modified"})
        return GitReadResult(ok=True, data={"files": files, "count": len(files)})

    def status(self) -> GitReadResult:
        branch = self.current_branch()
        changed = self.changed_files()
        if not branch.ok:
            return branch
        if not changed.ok:
            return changed
        return GitReadResult(
            ok=True,
            data={
                "branch": (branch.data or {}).get("branch"),
                "changed_files": (changed.data or {}).get("files", []),
                "changed_count": (changed.data or {}).get("count", 0),
            },
        )

    def recent_commits(self, limit: int = 5) -> GitReadResult:
        safe_limit = max(1, min(int(limit or 5), 20))
        result = self._run(["log", f"-n{safe_limit}", "--pretty=format:%h%x09%s"])
        if not result.ok:
            return result
        commits = []
        for line in str(result.data or "").splitlines():
            sha, _, subject = line.partition("\t")
            if sha:
                commits.append({"sha": sha, "subject": subject})
        return GitReadResult(ok=True, data={"commits": commits, "count": len(commits)})
