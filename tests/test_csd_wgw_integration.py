from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.services.context_delivery_service import ContextDeliveryService
from agent.services.context_file_selector import provider_to_llm_scope
from agent.services.workspace_context_policy import WorkspaceContextPolicy


class TestSelectiveDeliveryWithGitWorkspace:
    def test_selective_delivery_stages_files(self, tmp_path):
        import subprocess as sp

        sp.run(["git", "init", str(tmp_path)], capture_output=True)
        sp.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], capture_output=True)
        sp.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], capture_output=True)

        src = tmp_path / "src"
        src.mkdir()
        (src / "file1.py").write_text("# one")
        (src / "file2.py").write_text("# two")
        (src / "file3.py").write_text("# three")

        git_ctx = MagicMock()
        workspace_ctx = MagicMock()
        workspace_ctx.workspace_dir = tmp_path
        workspace_ctx.context_policy = WorkspaceContextPolicy(scope_mode="selective", max_files=200)
        workspace_ctx.git_context = git_ctx

        chunks = [
            {"path": "file1.py", "sensitivity": "public", "score": 1.0},
            {"path": "file2.py", "sensitivity": "public", "score": 0.9},
            {"path": "file3.py", "sensitivity": "public", "score": 0.8},
        ]
        svc = ContextDeliveryService()
        task = {"id": "t1", "effective_config": {}}

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=src):
                with patch.object(svc, "_resolve_llm_scope", return_value="local_only"):
                    result = svc.deliver(task=task, workspace_ctx=workspace_ctx)

        assert len(result.delivered_paths) == 3
        for fname in ("file1.py", "file2.py", "file3.py"):
            assert (tmp_path / fname).exists()

    def test_sensitivity_blocked_file_not_delivered(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "secret.py").write_text("# secret")

        git_ctx = None
        workspace_ctx = MagicMock()
        workspace_ctx.workspace_dir = tmp_path
        workspace_ctx.context_policy = WorkspaceContextPolicy(scope_mode="selective", max_files=200, sensitivity_ceiling="confidential")
        workspace_ctx.git_context = git_ctx

        chunks = [{"path": "secret.py", "sensitivity": "secret", "score": 1.0}]
        svc = ContextDeliveryService()
        task = {"id": "t1", "effective_config": {}}

        with patch.object(svc, "_retrieve_chunks", return_value=chunks):
            with patch.object(svc, "_repo_root", return_value=src):
                with patch.object(svc, "_resolve_llm_scope", return_value="external_cloud_allowed"):
                    result = svc.deliver(task=task, workspace_ctx=workspace_ctx)

        assert "secret.py" not in result.delivered_paths
        assert not (tmp_path / "secret.py").exists()

    def test_full_scope_with_git_workspace_no_selective_delivery(self, tmp_path):
        git_ctx = MagicMock()
        workspace_ctx = MagicMock()
        workspace_ctx.workspace_dir = tmp_path
        workspace_ctx.context_policy = WorkspaceContextPolicy(scope_mode="full")
        workspace_ctx.git_context = git_ctx

        svc = ContextDeliveryService()
        task = {"id": "t1", "effective_config": {}}

        with patch.object(svc, "_retrieve_chunks") as mock_retrieve:
            result = svc.deliver(task=task, workspace_ctx=workspace_ctx)

        mock_retrieve.assert_not_called()
        assert result.delivered_paths == []
        assert result.policy_scope_mode == "full"

    def test_provider_to_llm_scope_ollama(self):
        assert provider_to_llm_scope("ollama", None) == "local_only"

    def test_provider_to_llm_scope_external(self):
        assert provider_to_llm_scope("openai", "https://api.openai.com") == "external_cloud_allowed"

    def test_selector_and_resolver_independently_importable(self):
        from agent.services.context_file_selector import ContextFileSelector
        from agent.services.workspace_context_policy import WorkspaceContextPolicyResolver
        assert ContextFileSelector is not None
        assert WorkspaceContextPolicyResolver is not None
