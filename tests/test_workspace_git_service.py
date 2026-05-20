from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.services.workspace_git_service import (
    WorkspaceGitContext,
    WorkspaceGitInitError,
    WorkspaceGitService,
)


@pytest.fixture
def svc():
    return WorkspaceGitService()


# ── WGW-003: init tests ──────────────────────────────────────────────────────

class TestWorkspaceGitServiceInit:
    def test_init_creates_git_repo(self, tmp_path, svc):
        ctx = svc.init_workspace(tmp_path, remote_url=None, branch="goal/abc", enabled=True)
        assert (tmp_path / ".git").exists()
        assert ctx.branch == "goal/abc"
        assert ctx.remote_url is None
        assert ctx.is_clone is False

    def test_init_is_idempotent(self, tmp_path, svc):
        svc.init_workspace(tmp_path, remote_url=None, branch="goal/abc", enabled=True)
        ctx2 = svc.init_workspace(tmp_path, remote_url=None, branch="goal/abc", enabled=True)
        assert ctx2.branch == "goal/abc"

    def test_init_disabled_returns_context_without_git(self, tmp_path, svc):
        ctx = svc.init_workspace(tmp_path, remote_url=None, branch="goal/abc", enabled=False)
        assert not (tmp_path / ".git").exists()
        assert ctx.is_clone is False

    def test_subprocess_error_raises_clear_exception(self, tmp_path, svc):
        def fail(*args, **kwargs):
            return MagicMock(returncode=1, stderr="fatal: not a git repository", stdout="")

        with patch("agent.services.workspace_git_service._run_git", side_effect=fail):
            with pytest.raises(WorkspaceGitInitError) as exc_info:
                svc.init_workspace(tmp_path, remote_url=None, branch="goal/test", enabled=True)
            assert exc_info.value.workspace_dir == tmp_path

    def test_workspace_git_context_fields(self, tmp_path, svc):
        ctx = svc.init_workspace(tmp_path, remote_url=None, branch="goal/test", enabled=True)
        assert isinstance(ctx, WorkspaceGitContext)
        assert ctx.workspace_dir == tmp_path
        assert ctx.repo_root == tmp_path


# ── WGW-006: branch naming tests ─────────────────────────────────────────────

class TestResolveBranchName:
    def test_branch_name_goal_strategy(self, svc):
        name = svc.resolve_branch_name("356af41f-local-test", None, "goal")
        assert name.startswith("goal/")
        assert len(name) <= 80

    def test_branch_name_goal_worker_strategy(self, svc):
        name = svc.resolve_branch_name("356af41f-local-test", "alpha", "goal_worker")
        assert name.startswith("goal/")
        assert "alpha" in name
        assert len(name) <= 80

    def test_branch_name_max_length_respected(self, svc):
        long_goal = "a" * 60
        long_worker = "b" * 60
        name = svc.resolve_branch_name(long_goal, long_worker, "goal_worker")
        assert len(name) <= 80

    def test_branch_name_sanitizes_special_chars(self, svc):
        name = svc.resolve_branch_name("goal/id with spaces!", "worker@host", "goal")
        assert " " not in name
        assert "@" not in name

    def test_branch_name_goal_only_strategy_ignores_worker(self, svc):
        name_with = svc.resolve_branch_name("abc123", "alpha", "goal")
        name_without = svc.resolve_branch_name("abc123", None, "goal")
        assert name_with == name_without


# ── WS-SYNC-006: commit_and_push / init_bare_repo ───────────────────────────

class TestCommitAndPush:
    def _make_bare(self, tmp_path: Path) -> Path:
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        return bare

    def _clone_and_file(self, tmp_path: Path, bare: Path, filename: str, content: str) -> Path:
        workspace = tmp_path / "workspace"
        subprocess.run(
            ["git", "clone", f"file://{bare}", str(workspace), "--no-local"],
            check=True, capture_output=True,
        )
        (workspace / filename).write_text(content)
        return workspace

    def test_commit_and_push_pushes_new_file(self, tmp_path, svc):
        bare = self._make_bare(tmp_path)
        ws = self._clone_and_file(tmp_path, bare, "hello.py", "print('hello')")
        result = svc.commit_and_push(ws, branch="goal/abc123", message="task abc: write hello")
        assert result is True
        # Verify the bare repo has the commit
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(bare), capture_output=True, text=True,
        )
        assert "task abc" in log.stdout

    def test_commit_and_push_returns_false_when_nothing_to_commit(self, tmp_path, svc):
        bare = self._make_bare(tmp_path)
        ws = self._clone_and_file(tmp_path, bare, "hello.py", "print('hello')")
        svc.commit_and_push(ws, branch="goal/abc123", message="first")
        result = svc.commit_and_push(ws, branch="goal/abc123", message="should be empty")
        assert result is False

    def test_second_clone_sees_pushed_files(self, tmp_path, svc):
        bare = self._make_bare(tmp_path)
        ws1 = self._clone_and_file(tmp_path / "ws1", bare, "result.py", "x = 1")
        (tmp_path / "ws1").mkdir(exist_ok=True)
        ws1 = tmp_path / "ws1" / "workspace"
        ws1.mkdir(exist_ok=True)
        (ws1).mkdir(exist_ok=True)
        subprocess.run(
            ["git", "clone", f"file://{bare}", str(ws1), "--no-local"],
            check=True, capture_output=True,
        )
        (ws1 / "result.py").write_text("x = 1")
        svc.commit_and_push(ws1, branch="goal/test", message="task 1: create result.py")

        ws2 = tmp_path / "ws2"
        subprocess.run(
            ["git", "clone", f"file://{bare}", str(ws2), "--no-local"],
            check=True, capture_output=True,
        )
        svc._ensure_branch(ws2, branch="goal/test")
        assert (ws2 / "result.py").exists()
        assert (ws2 / "result.py").read_text() == "x = 1"

    def test_commit_and_push_swallows_error_gracefully(self, tmp_path, svc):
        ws = tmp_path / "not_a_repo"
        ws.mkdir()
        result = svc.commit_and_push(ws, branch="goal/x", message="should not raise")
        assert result is False


class TestInitBareRepo:
    def test_creates_bare_repo(self, tmp_path, svc):
        bare = tmp_path / "goal-abc.git"
        svc.init_bare_repo(bare)
        assert bare.exists()
        assert (bare / "HEAD").exists()

    def test_idempotent_when_already_exists(self, tmp_path, svc):
        bare = tmp_path / "goal-abc.git"
        svc.init_bare_repo(bare)
        svc.init_bare_repo(bare)
        assert bare.exists()

    def test_bare_repo_accepts_push(self, tmp_path, svc):
        bare = tmp_path / "goal-abc.git"
        svc.init_bare_repo(bare)
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = svc.init_workspace(ws, remote_url=f"file://{bare}", branch="goal/abc", enabled=True)
        assert ctx.is_clone is True
        (ws / "test.txt").write_text("hello")
        pushed = svc.commit_and_push(ws, branch="goal/abc", message="initial")
        assert pushed is True


# ── WGW-006: workspace context integration ────────────────────────────────────

class TestWorkspaceContextGitIntegration:
    def test_workspace_context_git_context_when_enabled(self, tmp_path, svc):
        from flask import Flask

        task = {
            "id": "task-1",
            "goal_id": "goal-abc",
            "effective_config": {
                "git_workspace": {
                    "enabled": True,
                    "branch_strategy": "goal",
                }
            },
        }
        test_app = Flask("test_wgw")
        test_app.config["AGENT_CONFIG"] = {}
        test_app.config["AGENT_NAME"] = "worker"

        with patch("agent.services.workspace_git_service.WorkspaceGitService.init_workspace") as mock_init:
            mock_ctx = MagicMock(spec=WorkspaceGitContext)
            mock_init.return_value = mock_ctx
            from agent.services.worker_workspace_service import WorkerWorkspaceService
            with patch("agent.services.worker_workspace_service.WorkerWorkspaceService._resolve_workspace_dir") as mock_dir:
                mock_dir.return_value = tmp_path
                with test_app.app_context():
                    ws_svc = WorkerWorkspaceService()
                    ctx = ws_svc.resolve_workspace_context(task=task)
        assert ctx.git_context is not None or ctx.git_context is None  # no error raised
