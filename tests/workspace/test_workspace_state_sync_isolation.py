"""WS-SYNC-009: Prove that Task 2 only sees Task 1's files via explicit sync — not via shared filesystem.
WS-SYNC-003: TaskArtifactMaterializer unit tests.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── WS-SYNC-009: Isolation / Leakage ────────────────────────────────────────

class TestWorkspaceIsolation:
    """Task-scoped workspaces must not bleed files to sibling tasks without explicit sync."""

    def test_task1_file_absent_in_task2_workspace_without_sync(self, tmp_path):
        task1_ws = tmp_path / "worker-a" / "task-1"
        task2_ws = tmp_path / "worker-b" / "task-2"
        task1_ws.mkdir(parents=True)
        task2_ws.mkdir(parents=True)

        (task1_ws / "output.py").write_text("result = 42")

        assert not (task2_ws / "output.py").exists(), (
            "Task 2 must NOT see Task 1's file without explicit sync — "
            "shared-FS leakage would mask a missing sync mechanism"
        )

    def test_task2_sees_file_after_artifact_materialization(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        task2_ws = tmp_path / "worker-b" / "task-2"
        task2_ws.mkdir(parents=True)

        artifact_content = b"result = 42\n"
        artifact_id = "artifact-abc"

        fake_task1 = MagicMock()
        fake_task1.id = "task-1"
        fake_task1.status = "completed"
        fake_task1.assigned_agent_url = "http://worker-a:5000"
        fake_task1.artifact_refs = [
            {
                "kind": "workspace_file",
                "artifact_id": artifact_id,
                "workspace_relative_path": "output.py",
            }
        ]

        mat = TaskArtifactMaterializer()

        with patch("agent.services.task_artifact_materializer.TaskArtifactMaterializer._try_local", return_value=artifact_content):
            with patch("agent.repositories.tasks.TaskRepository.get_by_goal_id", return_value=[fake_task1]):
                manifest = mat.materialize_predecessor_artifacts(
                    goal_id="goal-123",
                    task_id="task-2",
                    workspace_dir=task2_ws,
                )

        assert (task2_ws / "output.py").exists()
        assert (task2_ws / "output.py").read_bytes() == artifact_content
        assert len(manifest) == 1
        assert manifest[0]["artifact_id"] == artifact_id
        assert manifest[0]["source_task_id"] == "task-1"
        assert manifest[0]["relative_path"] == "output.py"

    def test_materializer_skips_own_task_artifacts(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        workspace = tmp_path / "ws"
        workspace.mkdir()

        same_task = MagicMock()
        same_task.id = "task-1"
        same_task.status = "completed"
        same_task.artifact_refs = [
            {"kind": "workspace_file", "artifact_id": "art-1", "workspace_relative_path": "output.py"}
        ]

        mat = TaskArtifactMaterializer()
        with patch("agent.repositories.tasks.TaskRepository.get_by_goal_id", return_value=[same_task]):
            manifest = mat.materialize_predecessor_artifacts(
                goal_id="goal-123",
                task_id="task-1",  # same as the task in the list
                workspace_dir=workspace,
            )

        assert manifest == []
        assert not (workspace / "output.py").exists()

    def test_materializer_skips_non_completed_tasks(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        workspace = tmp_path / "ws"
        workspace.mkdir()

        pending_task = MagicMock()
        pending_task.id = "task-1"
        pending_task.status = "running"
        pending_task.artifact_refs = [
            {"kind": "workspace_file", "artifact_id": "art-1", "workspace_relative_path": "output.py"}
        ]

        mat = TaskArtifactMaterializer()
        with patch("agent.repositories.tasks.TaskRepository.get_by_goal_id", return_value=[pending_task]):
            manifest = mat.materialize_predecessor_artifacts(
                goal_id="goal-123",
                task_id="task-2",
                workspace_dir=workspace,
            )

        assert manifest == []

    def test_materializer_rejects_path_traversal(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside.py"

        evil_task = MagicMock()
        evil_task.id = "task-evil"
        evil_task.status = "completed"
        evil_task.assigned_agent_url = ""
        evil_task.artifact_refs = [
            {
                "kind": "workspace_file",
                "artifact_id": "art-evil",
                "workspace_relative_path": "../outside.py",
            }
        ]

        mat = TaskArtifactMaterializer()
        with patch("agent.services.task_artifact_materializer.TaskArtifactMaterializer._try_local", return_value=b"evil"):
            with patch("agent.repositories.tasks.TaskRepository.get_by_goal_id", return_value=[evil_task]):
                mat.materialize_predecessor_artifacts(
                    goal_id="goal-123",
                    task_id="task-2",
                    workspace_dir=workspace,
                )

        assert not outside.exists(), "Path traversal must be blocked"

    def test_materializer_conflict_policy_keep_existing(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        workspace = tmp_path / "ws"
        workspace.mkdir()
        existing = workspace / "output.py"
        existing.write_text("local_version = True")

        task1 = MagicMock()
        task1.id = "task-1"
        task1.status = "completed"
        task1.assigned_agent_url = ""
        task1.artifact_refs = [
            {"kind": "workspace_file", "artifact_id": "art-1", "workspace_relative_path": "output.py"}
        ]

        mat = TaskArtifactMaterializer()
        with patch("agent.services.task_artifact_materializer.TaskArtifactMaterializer._try_local", return_value=b"remote_version = True"):
            with patch("agent.repositories.tasks.TaskRepository.get_by_goal_id", return_value=[task1]):
                mat.materialize_predecessor_artifacts(
                    goal_id="goal-123",
                    task_id="task-2",
                    workspace_dir=workspace,
                    conflict_policy="keep_existing",
                )

        assert existing.read_text() == "local_version = True"

    def test_materializer_only_injects_workspace_file_kind(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        workspace = tmp_path / "ws"
        workspace.mkdir()

        task1 = MagicMock()
        task1.id = "task-1"
        task1.status = "completed"
        task1.assigned_agent_url = ""
        task1.artifact_refs = [
            # workspace_diff kind should be skipped
            {"kind": "workspace_diff", "artifact_id": "art-diff", "workspace_relative_path": "changes.diff"},
            # task_output kind should be skipped
            {"kind": "task_output", "artifact_id": "art-out"},
        ]

        mat = TaskArtifactMaterializer()
        with patch("agent.repositories.tasks.TaskRepository.get_by_goal_id", return_value=[task1]):
            manifest = mat.materialize_predecessor_artifacts(
                goal_id="goal-123",
                task_id="task-2",
                workspace_dir=workspace,
            )

        assert manifest == []
        assert not (workspace / "changes.diff").exists()


# ── WS-SYNC-003: Artifact fetch fallback chain ───────────────────────────────

class TestArtifactFetchFallback:
    def test_local_path_used_when_file_exists(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        artifact_file = tmp_path / "v0001__result.py"
        artifact_file.write_bytes(b"x = 1")

        fake_version = MagicMock()
        fake_version.storage_path = str(artifact_file)
        fake_version.version_number = 1

        mat = TaskArtifactMaterializer()
        with patch("agent.repository.artifact_version_repo.get_by_artifact", return_value=[fake_version]):
            content = mat._try_local("art-123")

        assert content == b"x = 1"

    def test_try_local_returns_none_when_file_missing(self, tmp_path):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        fake_version = MagicMock()
        fake_version.storage_path = str(tmp_path / "nonexistent.py")
        fake_version.version_number = 1

        mat = TaskArtifactMaterializer()
        with patch("agent.repository.artifact_version_repo.get_by_artifact", return_value=[fake_version]):
            content = mat._try_local("art-missing")

        assert content is None

    def test_try_http_returns_none_on_connection_error(self):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer
        import requests

        mat = TaskArtifactMaterializer()
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            content = mat._try_http("http://unreachable:9999", "art-123")

        assert content is None

    def test_try_http_returns_none_on_404(self):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer

        fake_resp = MagicMock()
        fake_resp.ok = False
        fake_resp.status_code = 404

        mat = TaskArtifactMaterializer()
        with patch("requests.get", return_value=fake_resp):
            content = mat._try_http("http://worker-a:5000", "art-missing")

        assert content is None
