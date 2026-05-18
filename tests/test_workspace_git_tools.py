from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from flask import Flask

_test_flask_app = Flask("test_git_tools")


class TestGitToolsCwd:
    def _import_tools(self):
        from agent.tools import git_commit_tool, git_push_tool, git_status_tool
        return git_status_tool, git_commit_tool, git_push_tool

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("agent.tools._git_cwd", return_value="/test/workspace")
    @patch("subprocess.run")
    def test_git_status_uses_workspace_cwd(self, mock_run, mock_cwd, mock_check):
        mock_run.return_value = MagicMock(returncode=0, stdout="On branch main")
        from agent.tools import git_status_tool
        git_status_tool()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("cwd") == "/test/workspace" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[-1] == "/test/workspace"
        )

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("agent.tools._git_cwd", return_value="/test/workspace")
    @patch("subprocess.run")
    def test_git_commit_uses_workspace_cwd(self, mock_run, mock_cwd, mock_check):
        mock_run.return_value = MagicMock(returncode=0, stdout="[main abc123] feat: test")
        from agent.tools import git_commit_tool
        git_commit_tool("feat(test): valid message")
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("cwd") == "/test/workspace"

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("agent.tools._git_cwd", return_value=None)
    @patch("subprocess.run")
    def test_git_tools_fallback_without_workspace_context(self, mock_run, mock_cwd, mock_check):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        from agent.tools import git_status_tool
        result = git_status_tool()
        assert "error" not in result
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("cwd") is None


class TestGitCommitValidation:
    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("subprocess.run")
    def test_git_commit_rejects_blocked_message(self, mock_run, mock_check):
        from agent.tools import git_commit_tool
        result = git_commit_tool("fixup planning")
        assert result.get("error") == "invalid_commit_message"
        mock_run.assert_not_called()

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("subprocess.run")
    def test_git_commit_rejects_wip(self, mock_run, mock_check):
        from agent.tools import git_commit_tool
        result = git_commit_tool("wip")
        assert result.get("error") == "invalid_commit_message"
        mock_run.assert_not_called()

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("agent.tools._git_cwd", return_value=None)
    @patch("subprocess.run")
    def test_git_commit_accepts_valid_message(self, mock_run, mock_cwd, mock_check):
        mock_run.return_value = MagicMock(returncode=0, stdout="[main abc] feat: done")
        with patch("agent.common.audit.log_audit"):
            from agent.tools import git_commit_tool
            result = git_commit_tool("feat(goal-config): add key allowlist")
        assert result.get("status") == "success"
        mock_run.assert_called_once()

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("subprocess.run")
    def test_no_verify_flag_never_used(self, mock_run, mock_check):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        with patch("agent.tools._git_cwd", return_value=None):
            with patch("agent.common.audit.log_audit"):
                from agent.tools import git_commit_tool
                git_commit_tool("feat(test): valid message no verify check")
        all_args = []
        for c in mock_run.call_args_list:
            all_args.extend(c.args[0] if c.args else [])
        assert "--no-verify" not in all_args


class TestGitPushTool:
    @patch("agent.tools._check_git_access", return_value=(True, ""))
    def test_git_push_without_remote_returns_error(self, mock_check):
        with patch("agent.tools._git_cwd", return_value=None):
            with patch("flask.has_request_context", return_value=False):
                from agent.tools import git_push_tool
                result = git_push_tool()
        assert result.get("error") == "no_remote_configured"

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("subprocess.run")
    def test_git_push_never_uses_force(self, mock_run, mock_check):
        from flask import g
        mock_run.return_value = MagicMock(returncode=0, stdout="pushed")
        git_ctx = MagicMock()
        git_ctx.remote_url = "http://hub/repo.git"
        git_ctx.branch = "goal/abc123"
        with patch("agent.tools._git_cwd", return_value="/ws"):
            with _test_flask_app.test_request_context():
                g.git_context = git_ctx
                with patch("agent.common.audit.log_audit"):
                    from agent.tools import git_push_tool
                    git_push_tool()
        all_args = []
        for c in mock_run.call_args_list:
            all_args.extend(c.args[0] if c.args else [])
        assert "--force" not in all_args

    @patch("agent.tools._check_git_access", return_value=(True, ""))
    @patch("subprocess.run")
    def test_git_push_uses_correct_branch(self, mock_run, mock_check):
        from flask import g
        mock_run.return_value = MagicMock(returncode=0, stdout="pushed")
        git_ctx = MagicMock()
        git_ctx.remote_url = "http://hub/repo.git"
        git_ctx.branch = "goal/abc123"
        with patch("agent.tools._git_cwd", return_value="/ws"):
            with _test_flask_app.test_request_context():
                g.git_context = git_ctx
                with patch("agent.common.audit.log_audit"):
                    from agent.tools import git_push_tool
                    result = git_push_tool()
        assert result.get("status") == "success"
        call_args = mock_run.call_args.args[0]
        assert "goal/abc123" in call_args
