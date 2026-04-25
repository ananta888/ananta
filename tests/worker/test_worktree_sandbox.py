from __future__ import annotations

import subprocess
from pathlib import Path

from worker.coding.worktree_sandbox import WorktreeSandboxManager


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, text=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "worker-tests@example.local"], cwd=path)
    _run(["git", "config", "user.name", "worker-tests"], cwd=path)
    (path / "sample.txt").write_text("base\n", encoding="utf-8")
    _run(["git", "add", "sample.txt"], cwd=path)
    _run(["git", "commit", "-m", "init"], cwd=path)


def test_worktree_sandbox_isolated_from_main_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)

    manager = WorktreeSandboxManager(repository_root=repo)
    sandbox = manager.create(task_id="AW-T06")

    try:
        (sandbox.worktree_path / "sample.txt").write_text("changed\n", encoding="utf-8")
        changed = sandbox.changed_files()
        assert "sample.txt" in changed
        assert (repo / "sample.txt").read_text(encoding="utf-8") == "base\n"
    finally:
        sandbox.cleanup()

    assert not sandbox.worktree_path.exists()
