from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worker.coding.diff_builder import build_unified_diff
from worker.coding.patch_apply import apply_patch_artifact


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, text=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "worker-tests@example.local"], cwd=path)
    _run(["git", "config", "user.name", "worker-tests"], cwd=path)
    (path / "app.txt").write_text("v1\n", encoding="utf-8")
    _run(["git", "add", "app.txt"], cwd=path)
    _run(["git", "commit", "-m", "init"], cwd=path)


def test_patch_apply_requires_matching_approval_for_approval_required_policy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)

    (repo / "app.txt").write_text("v2\n", encoding="utf-8")
    patch_artifact = build_unified_diff(repository_root=repo).as_artifact(
        task_id="AW-T08",
        capability_id="worker.patch.apply",
        risk_classification="critical",
    )
    _run(["git", "checkout", "--", "app.txt"], cwd=repo)

    with pytest.raises(PermissionError, match="approval_required"):
        apply_patch_artifact(
            repository_root=repo,
            patch_artifact=patch_artifact,
            task_id="AW-T08",
            capability_id="worker.patch.apply",
            context_hash="ctx-1",
            policy_decision="approval_required",
            approval=None,
        )

    approval = {
        "status": "approved",
        "task_id": "AW-T08",
        "capability_id": "worker.patch.apply",
        "context_hash": "ctx-1",
        "patch_hash": patch_artifact["patch_hash"],
    }
    result = apply_patch_artifact(
        repository_root=repo,
        patch_artifact=patch_artifact,
        task_id="AW-T08",
        capability_id="worker.patch.apply",
        context_hash="ctx-1",
        policy_decision="approval_required",
        approval=approval,
    )
    assert result["status"] == "applied"
    assert "app.txt" in result["changed_files"]
    assert (repo / "app.txt").read_text(encoding="utf-8") == "v2\n"


def test_policy_denied_path_does_not_execute_git_apply(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)
    patch_artifact = {
        "schema": "patch_artifact.v1",
        "patch": "diff --git a/app.txt b/app.txt\n--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-v1\n+v2\n",
        "patch_hash": "invalid",
        "changed_files": ["app.txt"],
    }
    calls: list[list[str]] = []

    def _fail_if_called(args, *, cwd, input_text=None):  # noqa: ANN001, ANN003
        calls.append(args)
        raise AssertionError("git apply should not execute")

    monkeypatch.setattr("worker.coding.patch_apply._run_git", _fail_if_called)
    with pytest.raises(PermissionError, match="policy_denied"):
        apply_patch_artifact(
            repository_root=repo,
            patch_artifact=patch_artifact,
            task_id="AW-T08",
            capability_id="worker.patch.apply",
            context_hash="ctx-1",
            policy_decision="deny",
            approval=None,
        )
    assert calls == []


def test_guarded_roots_require_approval_even_when_policy_allows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _init_git_repo(repo)

    (repo / "app.txt").write_text("v2\n", encoding="utf-8")
    patch_artifact = build_unified_diff(repository_root=repo).as_artifact(
        task_id="AW-T08",
        capability_id="worker.patch.apply",
        risk_classification="critical",
    )
    _run(["git", "checkout", "--", "app.txt"], cwd=repo)

    with pytest.raises(PermissionError, match="approval_required"):
        apply_patch_artifact(
            repository_root=repo,
            patch_artifact=patch_artifact,
            task_id="AW-T08",
            capability_id="worker.patch.apply",
            context_hash="ctx-1",
            policy_decision="allow",
            guarded_roots=["app.txt"],
            approval=None,
        )
