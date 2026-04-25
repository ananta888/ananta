from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worker.coding.diff_builder import build_unified_diff
from worker.coding.patch_apply import apply_patch_artifact
from worker.coding.rollback import build_patch_failure_artifact, rollback_sandbox_state


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, text=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "worker-tests@example.local"], cwd=path)
    _run(["git", "config", "user.name", "worker-tests"], cwd=path)
    (path / "app.txt").write_text("line=1\n", encoding="utf-8")
    _run(["git", "add", "app.txt"], cwd=path)
    _run(["git", "commit", "-m", "init"], cwd=path)


def test_patch_failure_artifact_captures_patch_conflict_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)

    (repo / "app.txt").write_text("line=2\n", encoding="utf-8")
    patch_artifact = build_unified_diff(repository_root=repo).as_artifact(
        task_id="AW-T09",
        capability_id="worker.patch.apply",
        risk_classification="high",
    )
    _run(["git", "checkout", "--", "app.txt"], cwd=repo)
    (repo / "app.txt").write_text("line=conflict\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        apply_patch_artifact(
            repository_root=repo,
            patch_artifact=patch_artifact,
            task_id="AW-T09",
            capability_id="worker.patch.apply",
            context_hash="ctx-9",
            policy_decision="allow",
        )

    failure = build_patch_failure_artifact(
        task_id="AW-T09",
        patch_artifact=patch_artifact,
        error_message="patch_apply_conflict",
        changed_files=["app.txt"],
    )
    assert failure["schema"] == "patch_failure_artifact.v1"
    assert failure["patch_hash"] == patch_artifact["patch_hash"]
    assert "rollback_guidance" in failure


def test_invalid_patch_hash_and_safe_rollback_guard(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)
    patch_artifact = {
        "schema": "patch_artifact.v1",
        "patch": "diff --git a/app.txt b/app.txt\n--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-line=1\n+line=2\n",
        "patch_hash": "wrong-hash",
        "changed_files": ["app.txt"],
    }
    with pytest.raises(ValueError, match="patch_hash_mismatch"):
        apply_patch_artifact(
            repository_root=repo,
            patch_artifact=patch_artifact,
            task_id="AW-T09",
            capability_id="worker.patch.apply",
            context_hash="ctx-9",
            policy_decision="allow",
        )

    with pytest.raises(ValueError, match="unsafe_rollback_target"):
        rollback_sandbox_state(repository_root=repo, force=False)

    (repo / "app.txt").write_text("mutated\n", encoding="utf-8")
    rolled_back = rollback_sandbox_state(repository_root=repo, force=True)
    assert rolled_back["status"] == "rolled_back"
    assert (repo / "app.txt").read_text(encoding="utf-8") == "line=1\n"
