from flask import Flask

from agent.services.worker_workspace_service import WorkerWorkspaceService


def test_goal_scoped_workspace_same_goal_same_dir(tmp_path):
    app = Flask("wgw")
    app.config["AGENT_NAME"] = "worker-a"
    app.config["AGENT_CONFIG"] = {"worker_runtime": {"workspace_root": str(tmp_path), "workspace_reuse_mode": "goal_worker"}}

    svc = WorkerWorkspaceService()
    with app.app_context():
        ctx1 = svc.resolve_workspace_context(task={"id": "t1", "goal_id": "g1", "current_worker_job_id": "w1"})
        ctx2 = svc.resolve_workspace_context(task={"id": "t2", "goal_id": "g1", "current_worker_job_id": "w2"})
        ctx3 = svc.resolve_workspace_context(task={"id": "t3", "goal_id": "g2", "current_worker_job_id": "w3"})

    assert str(ctx1.workspace_dir) == str(ctx2.workspace_dir)
    assert str(ctx1.workspace_dir) != str(ctx3.workspace_dir)


def test_output_dir_lock_for_shared_workspace(tmp_path):
    app = Flask("wgw-lock")
    app.config["AGENT_NAME"] = "worker-a"
    app.config["AGENT_CONFIG"] = {"worker_runtime": {"workspace_root": str(tmp_path), "workspace_reuse_mode": "goal_worker"}}

    svc = WorkerWorkspaceService()
    task = {"id": "t1", "goal_id": "g1", "current_worker_job_id": "w1"}
    with app.app_context():
        ctx = svc.resolve_workspace_context(task=task)
        ok1, _ = svc.acquire_output_dir_lock(task=task, workspace_dir=ctx.workspace_dir)
        ok2, reason2 = svc.acquire_output_dir_lock(task=task, workspace_dir=ctx.workspace_dir)
        svc.release_output_dir_lock(task=task, workspace_dir=ctx.workspace_dir)

    assert ok1 is True
    assert ok2 is False
    assert reason2 == "workspace_write_conflict"
