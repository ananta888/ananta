from __future__ import annotations

import subprocess
from pathlib import Path

from worker.coding.diff_builder import build_unified_diff
from worker.coding.patch_planner import create_patch_plan


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, text=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "worker-tests@example.local"], cwd=path)
    _run(["git", "config", "user.name", "worker-tests"], cwd=path)
    (path / "a.txt").write_text("a0\n", encoding="utf-8")
    (path / "b.txt").write_text("b0\n", encoding="utf-8")
    _run(["git", "add", "a.txt", "b.txt"], cwd=path)
    _run(["git", "commit", "-m", "init"], cwd=path)


def test_patch_planner_creates_plan_artifact_before_apply() -> None:
    plan = create_patch_plan(
        task_id="AW-T07",
        capability_id="worker.patch.propose",
        target_files=["worker/coding/patch_planner.py"],
        expected_effects=["add patch planner"],
        context_refs=[{"source_id": "unit-test", "path": "tests/worker/test_patch_planner.py"}],
    )
    assert plan["schema"] == "worker_patch_plan.v1"
    assert plan["apply_state"] == "propose_only"
    assert plan["target_files"] == ["worker/coding/patch_planner.py"]


def test_diff_builder_covers_add_modify_delete_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)

    (repo / "a.txt").write_text("a1\n", encoding="utf-8")  # modify
    (repo / "b.txt").unlink()  # delete
    (repo / "c.txt").write_text("c1\n", encoding="utf-8")  # add (untracked)

    built = build_unified_diff(repository_root=repo, base_ref="HEAD")

    assert built.patch_hash
    assert "a.txt" in built.changed_files
    assert "b.txt" in built.changed_files
    assert "c.txt" in built.changed_files
    assert "diff --git a/a.txt b/a.txt" in built.diff
    assert "diff --git a/b.txt b/b.txt" in built.diff
    assert "diff --git a/c.txt b/c.txt" in built.diff
