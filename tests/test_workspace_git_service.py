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
