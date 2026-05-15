from pathlib import Path

import pytest
from flask import Flask

from agent.services.output_dir_lock_service import OutputDirLockService
from agent.services.worker_workspace_service import WorkerWorkspaceService


def test_output_dir_lock_blocks_parallel_writers():
    svc = OutputDirLockService()
    ok1, lease1, reason1 = svc.acquire(output_dir="/tmp/ananta-lock-test", owner="task-a", ttl_seconds=300)
    assert ok1 is True
    assert lease1 is not None
    assert reason1 is None

    ok2, lease2, reason2 = svc.acquire(output_dir="/tmp/ananta-lock-test", owner="task-b", ttl_seconds=300)
    assert ok2 is False
    assert lease2 is not None
    assert reason2 == "output_dir_busy"


def test_output_dir_lock_uses_canonical_path_identity(tmp_path):
    svc = OutputDirLockService()
    base = tmp_path / "w"
    base.mkdir(parents=True, exist_ok=True)
    alias = base / ".." / "w"

    ok1, _lease1, _reason1 = svc.acquire(output_dir=str(base), owner="task-a", ttl_seconds=300)
    ok2, _lease2, reason2 = svc.acquire(output_dir=str(alias), owner="task-b", ttl_seconds=300)

    assert ok1 is True
    assert ok2 is False
    assert reason2 == "output_dir_busy"


def test_workspace_service_rejects_output_dir_outside_workspace_root(tmp_path):
    app = Flask(__name__)
    app.config["AGENT_NAME"] = "worker"
    app.config["AGENT_CONFIG"] = {
        "worker_runtime": {"workspace_root": str(tmp_path / "root")},
        "output_dir_policy": {"unsafe_shared": False},
    }
    svc = WorkerWorkspaceService()
    task = {
        "id": "t1",
        "worker_execution_context": {"workspace": {"output_dir": str(tmp_path / "outside")}},
    }
    with app.app_context():
        with pytest.raises(ValueError, match="workspace_output_dir_outside_workspace_root"):
            svc.resolve_workspace_context(task=task)
