from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.services.context_delivery_service import (
    ContextDeliveryError,
    ContextDeliveryResult,
    ContextDeliveryService,
)
from agent.services.workspace_context_policy import WorkspaceContextPolicy


def _make_workspace_ctx(tmp_path: Path, scope_mode: str = "selective", git_context=None):
    ctx = MagicMock()
    ctx.workspace_dir = tmp_path
    ctx.context_policy = WorkspaceContextPolicy(scope_mode=scope_mode, max_files=200, sensitivity_ceiling="confidential")
    ctx.git_context = git_context
    return ctx


@pytest.fixture
def svc():
    return ContextDeliveryService()


class TestContextDeliveryService:
    def test_full_scope_returns_without_delivery(self, svc, tmp_path):
        ctx = _make_workspace_ctx(tmp_path, scope_mode="full")
        task = {"id": "t1", "effective_config": {}}
        result = svc.deliver(task=task, workspace_ctx=ctx)
        assert result.delivered_paths == []
        assert result.codecompass_profile_used is None
        assert result.policy_scope_mode == "full"

    def test_none_scope_returns_without_delivery(self, svc, tmp_path):
        ctx = _make_workspace_ctx(tmp_path, scope_mode="none")
        task = {"id": "t1"}
        result = svc.deliver(task=task, workspace_ctx=ctx)
        assert result.delivered_paths == []
        assert result.policy_scope_mode == "none"

    def test_selective_scope_copies_selected_files(self, svc, tmp_path):
        ctx = _make_workspace_ctx(tmp_path, scope_mode="selective")
        task = {"id": "t1", "task_kind": "coding", "effective_config": {}}

        src_file = tmp_path.parent / "repo" / "agent" / "foo.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# hello")

        chunks = [{"path": "agent/foo.py", "sensitivity": "public", "score": 1.0}]

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=tmp_path.parent / "repo"):
                with patch.object(svc, "_resolve_llm_scope", return_value="local_only"):
                    result = svc.deliver(task=task, workspace_ctx=ctx)

        assert "agent/foo.py" in result.delivered_paths

    def test_missing_source_file_goes_to_skipped(self, svc, tmp_path):
        ctx = _make_workspace_ctx(tmp_path, scope_mode="selective")
        task = {"id": "t1", "effective_config": {}}
        chunks = [{"path": "nonexistent/file.py", "sensitivity": "public", "score": 1.0}]

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=tmp_path / "fakerepo"):
                with patch.object(svc, "_resolve_llm_scope", return_value="local_only"):
                    result = svc.deliver(task=task, workspace_ctx=ctx)

        assert "nonexistent/file.py" in result.skipped_paths
        assert "nonexistent/file.py" not in result.delivered_paths

    def test_retrieval_error_raises_delivery_error(self, svc, tmp_path):
        ctx = _make_workspace_ctx(tmp_path, scope_mode="selective")
        task = {"id": "t1", "effective_config": {}}

        def bad_retrieve(**_):
            raise RuntimeError("CodeCompass unavailable")

        with patch.object(svc, "_retrieve_chunks", side_effect=bad_retrieve):
            with pytest.raises(ContextDeliveryError, match="context_delivery_failed"):
                svc.deliver(task=task, workspace_ctx=ctx)

    def test_git_add_called_when_git_context_present(self, svc, tmp_path):
        git_ctx = MagicMock()
        ctx = _make_workspace_ctx(tmp_path, scope_mode="selective", git_context=git_ctx)
        task = {"id": "t1", "effective_config": {}}

        repo_root = tmp_path / "repo"
        src_file = repo_root / "agent" / "foo.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# src")

        chunks = [{"path": "agent/foo.py", "sensitivity": "public", "score": 1.0}]

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=repo_root):
                with patch.object(svc, "_resolve_llm_scope", return_value="local_only"):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0, stderr="")
                        result = svc.deliver(task=task, workspace_ctx=ctx)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "git" in call_args.args[0]
        assert "add" in call_args.args[0]

    def test_git_add_not_called_when_no_git_context(self, svc, tmp_path):
        ctx = _make_workspace_ctx(tmp_path, scope_mode="selective", git_context=None)
        task = {"id": "t1", "effective_config": {}}

        src_file = tmp_path / "agent" / "bar.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# src")

        chunks = [{"path": "agent/bar.py", "sensitivity": "public", "score": 1.0}]

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=tmp_path):
                with patch.object(svc, "_resolve_llm_scope", return_value="local_only"):
                    with patch("subprocess.run") as mock_run:
                        svc.deliver(task=task, workspace_ctx=ctx)

        mock_run.assert_not_called()

    def test_git_add_failure_adds_warning_not_fatal(self, svc, tmp_path):
        git_ctx = MagicMock()
        ctx = _make_workspace_ctx(tmp_path, scope_mode="selective", git_context=git_ctx)
        task = {"id": "t1", "effective_config": {}}

        repo_root = tmp_path / "repo"
        src_file = repo_root / "agent" / "baz.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# src")

        chunks = [{"path": "agent/baz.py", "sensitivity": "public", "score": 1.0}]

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=repo_root):
                with patch.object(svc, "_resolve_llm_scope", return_value="local_only"):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stderr="not a git repo")
                        result = svc.deliver(task=task, workspace_ctx=ctx)

        assert len(result.warnings) > 0
        assert "agent/baz.py" in result.delivered_paths
