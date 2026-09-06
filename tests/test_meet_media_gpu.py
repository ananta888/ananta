"""Explicit real-hardware gate. Synthetic task scope, never production evidence."""

import base64
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.skipif(os.environ.get("MEET_MEDIA_GPU_GATE") != "1", reason="opt-in provisioned local RTX media worker")
def test_real_hub_task_and_local_gpu_response(app):
    from agent.repository import task_repo
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
    from worker.meet_media.contract import load_key

    worker = HttpMediaWorker(os.environ["MEET_MEDIA_GPU_ENDPOINT"], load_key(os.environ["MEET_MEDIA_GPU_KEY_FILE"]))
    runtime = MeetTurnService(Mock(), worker, HubMediaTasks(), [("synthetic-gpu", "synthetic-gpu")])
    principal = SimpleNamespace(tenant_id="synthetic-gpu", subject_id="synthetic-test-actor")
    with app.app_context():
        result = runtime.execute(principal, "synthetic-gpu", {"text": "Sage einen kurzen deutschen Begrüßungssatz."})
        task = task_repo.get_by_id(result["task_id"])
        assert task.status == "completed"
        assert task.worker_execution_context["meet_media"]["lease_id"] == result["lease_id"]
        assert task.tenant_id == "synthetic-gpu" and task.project_id == "synthetic-gpu"
        assert result["engines"]["speech"] == "piper-cuda"
        assert result["engines"]["video"] == "procedural-avatar-h264_nvenc"
        assert base64.b64decode(result["audio"]["base64"])[8:12] == b"WAVE"
        assert len(base64.b64decode(result["video"]["base64"])) > 1000
