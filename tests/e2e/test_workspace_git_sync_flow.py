"""WS-SYNC-008: Integration test — Task 1 commit+push, Task 2 clone sees the files.

Uses real git operations against a local bare repo (no docker, no hub process needed).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.services.workspace_git_service import WorkspaceGitService


@pytest.fixture
def svc():
    return WorkspaceGitService()


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    bare = tmp_path / "hub.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    return bare


class TestGitSyncFlowSequentialTasks:
    """Two sequential tasks sharing a bare hub repo, proving file handoff via git."""

    def test_task2_sees_file_written_by_task1(self, tmp_path: Path, svc: WorkspaceGitService, bare_repo: Path):
        remote_url = f"file://{bare_repo}"
        branch = "goal/testgoal"

        # Task 1: init workspace, write artifact, commit+push
        ws1 = tmp_path / "ws1"
        svc.init_workspace(ws1, remote_url=remote_url, branch=branch, enabled=True)
        (ws1 / "result.py").write_text("answer = 42\n")
        pushed = svc.commit_and_push(ws1, branch=branch, message="task t1: write result.py")
        assert pushed is True

        # Task 2: fresh clone from same remote — should see result.py
        ws2 = tmp_path / "ws2"
        svc.init_workspace(ws2, remote_url=remote_url, branch=branch, enabled=True)
        assert (ws2 / "result.py").exists(), "Task 2 workspace missing file written by Task 1"
        assert (ws2 / "result.py").read_text() == "answer = 42\n"

    def test_incremental_updates_visible_to_next_clone(self, tmp_path: Path, svc: WorkspaceGitService, bare_repo: Path):
        remote_url = f"file://{bare_repo}"
        branch = "goal/incremental"

        # Task 1: write file v1
        ws1 = tmp_path / "ws1"
        svc.init_workspace(ws1, remote_url=remote_url, branch=branch, enabled=True)
        (ws1 / "config.json").write_text('{"step": 1}\n')
        assert svc.commit_and_push(ws1, branch=branch, message="task t1: config step 1") is True

        # Task 2: clones, writes additional file, pushes
        ws2 = tmp_path / "ws2"
        svc.init_workspace(ws2, remote_url=remote_url, branch=branch, enabled=True)
        assert (ws2 / "config.json").exists(), "Task 2 should see config.json from Task 1"
        (ws2 / "output.txt").write_text("done\n")
        assert svc.commit_and_push(ws2, branch=branch, message="task t2: add output.txt") is True

        # Task 3: should see both files
        ws3 = tmp_path / "ws3"
        svc.init_workspace(ws3, remote_url=remote_url, branch=branch, enabled=True)
        assert (ws3 / "config.json").exists()
        assert (ws3 / "output.txt").exists()

    def test_nothing_pushed_when_no_changes(self, tmp_path: Path, svc: WorkspaceGitService, bare_repo: Path):
        remote_url = f"file://{bare_repo}"
        branch = "goal/noop"

        ws1 = tmp_path / "ws1"
        svc.init_workspace(ws1, remote_url=remote_url, branch=branch, enabled=True)
        (ws1 / "seed.py").write_text("x = 1\n")
        svc.commit_and_push(ws1, branch=branch, message="seed")

        # Task 2 clones but makes no changes
        ws2 = tmp_path / "ws2"
        svc.init_workspace(ws2, remote_url=remote_url, branch=branch, enabled=True)
        result = svc.commit_and_push(ws2, branch=branch, message="empty push")
        assert result is False, "commit_and_push should return False when nothing changed"

    def test_gitignore_written_on_clone(self, tmp_path: Path, svc: WorkspaceGitService, bare_repo: Path):
        remote_url = f"file://{bare_repo}"
        ws = tmp_path / "ws1"
        svc.init_workspace(ws, remote_url=remote_url, branch="goal/gi", enabled=True)
        assert (ws / ".gitignore").exists(), ".gitignore should be written after clone"
        content = (ws / ".gitignore").read_text()
        assert "__pycache__/" in content
        assert "artifacts/" in content


class TestHubGitRemoteService:
    """WS-SYNC-007 companion: HubGitRemoteService round-trip."""

    def test_create_goal_repo_and_get_url(self, tmp_path: Path):
        from agent.services.hub_git_remote_service import HubGitRemoteService
        svc = HubGitRemoteService(repos_root=tmp_path / "repos")
        repo_path = svc.create_goal_repo("abc123def456xyz")
        assert repo_path.exists()
        assert (repo_path / "HEAD").exists()
        url = svc.get_remote_url("abc123def456xyz")
        assert url.startswith("file://")
        assert "abc123def4" in url  # truncated to 12 chars

    def test_repo_exists_false_before_create(self, tmp_path: Path):
        from agent.services.hub_git_remote_service import HubGitRemoteService
        svc = HubGitRemoteService(repos_root=tmp_path / "repos")
        assert svc.repo_exists("no-such-goal") is False

    def test_create_is_idempotent(self, tmp_path: Path):
        from agent.services.hub_git_remote_service import HubGitRemoteService
        svc = HubGitRemoteService(repos_root=tmp_path / "repos")
        svc.create_goal_repo("goal1")
        svc.create_goal_repo("goal1")  # should not raise
        assert svc.repo_exists("goal1")

    def test_list_branches_empty_on_new_repo(self, tmp_path: Path):
        from agent.services.hub_git_remote_service import HubGitRemoteService
        svc = HubGitRemoteService(repos_root=tmp_path / "repos")
        svc.create_goal_repo("goal2")
        branches = svc.list_branches("goal2")
        assert isinstance(branches, list)

    def test_worker_push_visible_as_branch(self, tmp_path: Path):
        from agent.services.hub_git_remote_service import HubGitRemoteService
        hub_svc = HubGitRemoteService(repos_root=tmp_path / "repos")
        hub_svc.create_goal_repo("goal3")
        remote_url = hub_svc.get_remote_url("goal3")

        ws_svc = WorkspaceGitService()
        ws = tmp_path / "ws"
        ws_svc.init_workspace(ws, remote_url=remote_url, branch="goal/goal3", enabled=True)
        (ws / "work.py").write_text("# done\n")
        pushed = ws_svc.commit_and_push(ws, branch="goal/goal3", message="task x: work")
        assert pushed is True

        branches = hub_svc.list_branches("goal3")
        assert "goal/goal3" in branches
