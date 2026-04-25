from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


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
    return completed.stdout or ""


def build_patch_failure_artifact(
    *,
    task_id: str,
    patch_artifact: dict[str, Any],
    error_message: str,
    changed_files: list[str],
) -> dict[str, Any]:
    return {
        "schema": "patch_failure_artifact.v1",
        "task_id": str(task_id).strip(),
        "patch_hash": str(patch_artifact.get("patch_hash") or "").strip(),
        "error_message": str(error_message or "").strip(),
        "changed_files": [str(item).strip() for item in changed_files if str(item).strip()],
        "rollback_guidance": [
            "Inspect changed files and patch hash before retrying.",
            "Run rollback_sandbox_state on sandbox worktree when safe.",
            "Re-create patch proposal using updated base ref when conflicts persist.",
        ],
    }


def rollback_sandbox_state(*, repository_root: Path, force: bool = False) -> dict[str, Any]:
    repo = repository_root.resolve()
    # Avoid destructive reset on non-sandbox paths unless force is explicitly set.
    if not force and "ananta-worker-" not in str(repo):
        raise ValueError("unsafe_rollback_target")
    _run_git(["reset", "--hard", "HEAD"], cwd=repo)
    _run_git(["clean", "-fd"], cwd=repo)
    return {"status": "rolled_back", "repository_root": str(repo)}
