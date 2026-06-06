"""WS-SYNC-005 + APRL-018: End-to-end flow test — isolated workspaces, artifact injection,
and composed AGENTS.md / agent-profile.json in OpenCode workspaces.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def app(tmp_path):
    a = Flask("test_e2e_ws")
    a.config["AGENT_CONFIG"] = {
        "workspace": {"sync_mode": "artifact_hub_sync"},
        "worker_runtime": {"workspace_root": str(tmp_path / "workspaces")},
    }
    a.config["AGENT_NAME"] = "worker-alpha"
    return a


def _make_task(task_id: str, goal_id: str, worker_job_id: str) -> dict:
    return {
        "id": task_id,
        "goal_id": goal_id,
        "title": f"Task {task_id}",
        "current_worker_job_id": worker_job_id,
        "worker_execution_context": {
            "workspace": {
                "task_id": task_id,
                "worker_job_id": worker_job_id,
                "sync_mode": "artifact_hub_sync",
            }
        },
    }


class TestWorkspaceIsolationAndArtifactInjection:
    """Two tasks get separate workspace dirs. Task 2 sees Task 1's file only via materializer."""

    def test_separate_workspaces_no_bleed(self, app: Flask):
        from agent.services.worker_workspace_service import WorkerWorkspaceService

        task1 = _make_task("task-1", "goal-abc", "job-1")
        task2 = _make_task("task-2", "goal-abc", "job-2")

        svc = WorkerWorkspaceService()
        with app.app_context():
            with patch.object(svc, "_materialize_predecessor_artifacts", return_value=None):
                ctx1 = svc.resolve_workspace_context(task=task1)
                ctx2 = svc.resolve_workspace_context(task=task2)

        assert ctx1.workspace_dir != ctx2.workspace_dir, "Tasks must have distinct workspaces"
        (ctx1.workspace_dir / "secret.txt").write_text("task1 secret")
        assert not (ctx2.workspace_dir / "secret.txt").exists(), "No bleed without sync"

    def test_artifact_injection_populates_task2_workspace(self, app: Flask):
        from agent.services.task_artifact_materializer import TaskArtifactMaterializer
        from agent.services.worker_workspace_service import WorkerWorkspaceService

        task2 = _make_task("task-2", "goal-abc", "job-2")

        artifact_content = b"# produced by task 1\nresult = 99\n"
        fake_task1 = MagicMock()
        fake_task1.id = "task-1"
        fake_task1.status = "completed"
        fake_task1.assigned_agent_url = "http://worker-alpha:5000"
        fake_task1.verification_status = {
            "execution_artifacts": [
                {
                    "kind": "workspace_file",
                    "artifact_id": "art-xyz",
                    "workspace_relative_path": "result.py",
                }
            ]
        }

        svc = WorkerWorkspaceService()
        with app.app_context():
            with patch(
                "agent.repositories.tasks.TaskRepository.get_by_goal_id",
                return_value=[fake_task1],
            ):
                with patch(
                    "agent.services.task_artifact_materializer.TaskArtifactMaterializer._try_local",
                    return_value=artifact_content,
                ):
                    ctx2 = svc.resolve_workspace_context(task=task2)

        assert (ctx2.workspace_dir / "result.py").exists(), "Materializer must inject result.py"
        assert (ctx2.workspace_dir / "result.py").read_bytes() == artifact_content
        assert ctx2.materialization_manifest is not None
        assert len(ctx2.materialization_manifest) == 1

    def test_materialization_manifest_captured_in_context(self, app: Flask):
        from agent.services.worker_workspace_service import WorkerWorkspaceService

        task = _make_task("task-3", "goal-xyz", "job-3")

        fake_manifest = [{"artifact_id": "art-1", "relative_path": "a.py", "source_task_id": "task-0"}]
        svc = WorkerWorkspaceService()
        with app.app_context():
            with patch.object(svc, "_materialize_predecessor_artifacts", return_value=fake_manifest):
                ctx = svc.resolve_workspace_context(task=task)

        assert ctx.materialization_manifest == fake_manifest

    def test_no_materialization_when_sync_mode_none(self, tmp_path: Path):
        from agent.services.worker_workspace_service import WorkerWorkspaceService

        app_no_sync = Flask("test_no_sync")
        app_no_sync.config["AGENT_CONFIG"] = {
            "workspace": {"sync_mode": "none"},
            "worker_runtime": {"workspace_root": str(tmp_path / "workspaces")},
        }
        app_no_sync.config["AGENT_NAME"] = "worker-beta"

        task = {
            "id": "task-no-sync",
            "goal_id": "goal-x",
            "current_worker_job_id": "job-ns",
            "worker_execution_context": {
                "workspace": {"task_id": "task-no-sync", "worker_job_id": "job-ns", "sync_mode": "none"}
            },
        }

        svc = WorkerWorkspaceService()
        with app_no_sync.app_context():
            ctx = svc.resolve_workspace_context(task=task)

        assert ctx.materialization_manifest is None, "No manifest when sync_mode=none"


class TestWorkspaceStateSyncRecord:
    """WS-SYNC-004: verify _build_workspace_state_sync_record output shape."""

    def test_sync_record_fields(self):
        from agent.services.task_scoped_execution_service import _build_workspace_state_sync_record

        task = {
            "worker_execution_context": {
                "workspace": {"sync_mode": "artifact_hub_sync"}
            }
        }
        manifest = [{"artifact_id": "art-in", "workspace_relative_path": "in.py"}]
        refs = [{"kind": "workspace_file", "artifact_id": "art-out", "workspace_relative_path": "out.py"}]

        with patch("flask.has_app_context", return_value=False):
            record = _build_workspace_state_sync_record(
                task=task,
                materialization_manifest=manifest,
                workspace_artifact_refs=refs,
                git_pushed=True,
            )

        assert record["sync_mode"] == "artifact_hub_sync"
        assert record["source_of_truth"] == "hub_artifacts"
        assert record["git_pushed"] is True
        assert len(record["input_artifacts"]) == 1
        assert record["input_artifacts"][0]["artifact_id"] == "art-in"
        assert len(record["output_artifacts"]) == 1
        assert record["output_artifacts"][0]["artifact_id"] == "art-out"

    def test_sync_record_none_manifest(self):
        from agent.services.task_scoped_execution_service import _build_workspace_state_sync_record

        with patch("flask.has_app_context", return_value=False):
            record = _build_workspace_state_sync_record(
                task={},
                materialization_manifest=None,
                workspace_artifact_refs=[],
                git_pushed=False,
            )

        assert record["input_artifacts"] == []
        assert record["git_pushed"] is False


# ---------------------------------------------------------------------------
# APRL-018: OpenCode workspace contains composed AGENTS.md + agent-profile.json
# ---------------------------------------------------------------------------

def _make_opencode_task(task_kind: str = "bug_fix") -> dict:
    return {
        "id": "task-aprl-test",
        "goal_id": "goal-aprl-test",
        "title": f"APRL-018 test task ({task_kind})",
        "task_kind": task_kind,
        "current_worker_job_id": "job-aprl",
        "worker_execution_context": {
            "workspace": {
                "task_id": "task-aprl-test",
                "worker_job_id": "job-aprl",
                "sync_mode": "none",
            }
        },
    }


class TestOpenCodeAgentProfileIntegration:
    """APRL-018: workspace AGENTS.md is composed from Root + profile; agent-profile.json written."""

    def _prepare(self, tmp_path: Path, task: dict, app_: Flask):
        from agent.services.worker_workspace_service import WorkerWorkspaceService
        svc = WorkerWorkspaceService()
        with app_.app_context():
            with patch.object(svc, "_materialize_predecessor_artifacts", return_value=None):
                ctx = svc.resolve_workspace_context(task=task)

        manifest = svc.prepare_opencode_context_files(
            task=task,
            workspace_context=ctx,
            base_prompt="Fix the bug in service.py",
            system_prompt=None,
            context_text=None,
            expected_output_schema=None,
            tool_definitions=None,
            research_context=None,
            include_response_contract=True,
        )
        return ctx, manifest

    def test_agents_md_contains_global_agents_section(self, tmp_path, app):
        task = _make_opencode_task("bug_fix")
        ctx, manifest = self._prepare(tmp_path, task, app)
        agents_md = (ctx.workspace_dir / "AGENTS.md").read_text()
        assert "Global AGENTS" in agents_md

    def test_agents_md_contains_container_constraints(self, tmp_path, app):
        task = _make_opencode_task("bug_fix")
        ctx, manifest = self._prepare(tmp_path, task, app)
        agents_md = (ctx.workspace_dir / "AGENTS.md").read_text()
        assert "sudo" in agents_md
        assert "systemctl" in agents_md

    def test_agent_profile_json_written(self, tmp_path, app):
        task = _make_opencode_task("bug_fix")
        ctx, manifest = self._prepare(tmp_path, task, app)
        profile_json_path = ctx.workspace_dir / ".ananta" / "agent-profile.json"
        assert profile_json_path.exists(), ".ananta/agent-profile.json must be written"
        data = json.loads(profile_json_path.read_text())
        assert "profile_id" in data
        assert "agents_file" in data
        assert "checksums" in data

    def test_manifest_has_active_agent_profile_and_path(self, tmp_path, app):
        task = _make_opencode_task("bug_fix")
        ctx, manifest = self._prepare(tmp_path, task, app)
        assert "agent_profile_path" in manifest, "manifest must have agent_profile_path key"
        assert "active_agent_profile" in manifest, "manifest must have active_agent_profile key"
        assert manifest["active_agent_profile"]["profile_id"] is not None

    def test_context_index_lists_agent_profile_json(self, tmp_path, app):
        task = _make_opencode_task("bug_fix")
        ctx, manifest = self._prepare(tmp_path, task, app)
        context_index = (ctx.workspace_dir / ".ananta" / "context-index.md").read_text()
        assert "agent-profile.json" in context_index

    def test_task_brief_contains_active_profile(self, tmp_path, app):
        task = _make_opencode_task("bug_fix")
        ctx, manifest = self._prepare(tmp_path, task, app)
        task_brief = (ctx.workspace_dir / ".ananta" / "task-brief.md").read_text()
        assert "Active agent profile" in task_brief

    def test_root_only_fallback_when_no_task_kind(self, tmp_path, app):
        task = _make_opencode_task("")
        task["task_kind"] = ""
        ctx, manifest = self._prepare(tmp_path, task, app)
        profile_data = manifest.get("active_agent_profile") or {}
        # Should be root_only fallback (or whatever matches empty task_kind)
        # The important thing: no exception and profile_id is set
        assert "profile_id" in profile_data

    def test_container_constraints_always_present_even_with_profile(self, tmp_path, app):
        task = _make_opencode_task("refactor")
        ctx, manifest = self._prepare(tmp_path, task, app)
        agents_md = (ctx.workspace_dir / "AGENTS.md").read_text()
        assert "sudo" in agents_md, "Container constraints must survive profile composition"
