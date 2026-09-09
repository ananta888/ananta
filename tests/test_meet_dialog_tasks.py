"""Ordinary isolated test-database TaskQueue, not a production rollout gate."""

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from agent.services.meet_dialog_controls import initial_controls
from agent.services.meet_dialog_tasks import HubDialogTasks

pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize("worker_url", [None, "http://synthetic-audio-worker:8094"])
def test_real_hub_tasks_claim_one_audio_job_and_cleanup_after_parent_expiry(app, worker_url):
    tasks = HubDialogTasks()
    task_id = str(uuid.uuid4())
    now = int(time.time())
    context = {
        "lease_id": str(uuid.uuid4()),
        "runtime_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "room_id": "room-" + "a" * 18,
        "owner_subject": "synthetic-owner",
        "binding_task_id": "",
        "deadline": now + 600,
        "capabilities": ["audio.receive", "chat.send"],
        "chat_mode": "off",
        "audio_mode": "transcribe",
        "audio_job": None,
        "audio_count": 0,
    }
    context["controls"] = initial_controls(context["capabilities"], "off", "transcribe", now * 1000)
    scope = SimpleNamespace(
        task_id=task_id,
        tenant_id="synthetic-tenant",
        project_id="synthetic-project",
        owner_subject="synthetic-owner",
        lease_id=context["lease_id"],
        runtime_id=context["runtime_id"],
    )
    job = {
        "task_id": str(uuid.uuid4()),
        "lease_id": str(uuid.uuid4()),
        "issued_at": now,
        "deadline": now + 30,
        "control_revision": 1,
        "meet_session_id": "ms_" + "a" * 32,
        "generation": 1,
        "membership_epoch": 2,
        "receive_revision": 3,
        "peer_id": "a" * 16,
        "own_peer_id": "b" * 16,
        "publication_id": "synthetic-audio",
        "publication_epoch": 4,
        "source": "microphone",
    }
    with app.app_context():
        tasks.start(task_id, scope.tenant_id, scope.project_id, context)
        if worker_url is not None:
            from sqlmodel import Session

            from agent.database import engine
            from agent.db_models import AgentInfoDB
            from agent.services.task_runtime_service import compare_and_set_local_task_status

            with Session(engine) as session:
                session.add(AgentInfoDB(url=worker_url, name="Synthetic audio worker"))
                session.commit()
            assert compare_and_set_local_task_status(
                task_id, "in_progress", expected_statuses={"in_progress"}, assigned_agent_url=worker_url
            )
        parent = tasks.get_by_id(task_id)
        assert parent.status == "in_progress" and parent.task_kind == "meet_dialog_session"
        tasks.claim_audio(scope, job, now)
        child = tasks.get_by_id(job["task_id"])
        assert (
            child.status == "in_progress"
            and child.parent_task_id == task_id
            and child.task_kind == "meet_audio_receive"
        )
        assert child.assigned_agent_url == parent.assigned_agent_url == worker_url
        with pytest.raises(Exception, match="meet_audio_job_busy_or_exhausted"):
            tasks.claim_audio(scope, job | {"task_id": str(uuid.uuid4())}, now)
        assert tasks.finish_bound(task_id, "wrong-dispatch", scope.runtime_id, "cancelled") is False
        assert tasks.get_by_id(task_id).status == "in_progress"
        assert tasks.finish_bound(task_id, scope.lease_id, scope.runtime_id, "cancelled") is True
        assert tasks.get_by_id(task_id).status == "cancelled"
        assert tasks.get_by_id(job["task_id"]).status == "cancelled"
        encoded = json.dumps(tasks.get_by_id(task_id).model_dump(), default=str)
        assert "synthetic private transcript" not in encoded and "pcmBase64" not in encoded
