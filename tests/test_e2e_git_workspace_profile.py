from flask import Flask

from agent.services.worker_workspace_service import WorkerWorkspaceService
from agent.services.workspace_git_service import WorkspaceGitService


def test_git_workspace_optional_disabled(tmp_path):
    app = Flask("git-off")
    app.config["AGENT_NAME"] = "worker-a"
    app.config["AGENT_CONFIG"] = {"worker_runtime": {"workspace_root": str(tmp_path)}}
    svc = WorkerWorkspaceService()
    with app.app_context():
        ctx = svc.resolve_workspace_context(task={"id": "t1", "goal_id": "g1", "effective_config": {"git_workspace": {"enabled": False}}})
    assert ctx.git_context is None


def test_git_workspace_goal_branch_strategy():
    name = WorkspaceGitService().resolve_branch_name("goal-123", "worker-a", "goal")
    assert name.startswith("goal/")
