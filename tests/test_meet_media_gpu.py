"""Explicit real-hardware gate. Synthetic task scope, never production evidence."""

import base64
import os
import time
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


@pytest.mark.skipif(os.environ.get("MEET_MEDIA_GPU_GATE") != "1", reason="opt-in provisioned local RTX media worker")
@pytest.mark.timeout(150)
def test_real_gpu_chat_reply_obeys_reserved_budget(app, tmp_path):
    from sqlalchemy import create_engine

    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.repository import task_repo
    from agent.services.meet_chat_admission import MeetChatAdmissionService
    from agent.services.meet_chat_policy import ChatReplyPolicy
    from agent.services.meet_chat_reply_service import MeetChatReplyService
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.meet_turn_service import HubMediaTasks
    from tests.test_meet_chat_admission import raw, session
    from worker.meet_media.contract import load_key

    engine = create_engine(f"sqlite:///{tmp_path / 'gpu-admission.sqlite'}")
    reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
    reservations.initialize()
    dispatches.initialize()
    authority = Mock()
    authority.current.return_value = session(
        ChatReplyPolicy(mode="mention", max_reply_chars=80, max_output_tokens=32),
        deadline_ms=int(time.time() * 1000) + 115_000,
    )
    admission = MeetChatAdmissionService(authority, reservations).admit(
        raw(sent_at_ms=int(time.time() * 1000), text="@ananta Sage kurz Hallo auf Deutsch.")
    )
    worker = HttpMediaWorker(os.environ["MEET_MEDIA_GPU_ENDPOINT"], load_key(os.environ["MEET_MEDIA_GPU_KEY_FILE"]))
    service = MeetChatReplyService(authority, dispatches, Mock(), worker, HubMediaTasks())
    try:
        with app.app_context():
            reply = service.execute(SimpleNamespace(tenant_id="tenant", subject_id="synthetic-test-actor"), admission)
            assert reply["published"] is False
            assert 0 < len(reply["media"]["text"]) <= 80
            assert 0 < reply["media"]["usage"]["output_tokens"] <= 32
            task = task_repo.get_by_id(reply["media"]["task_id"])
            assert task.status == "completed"
            assert (
                task.worker_execution_context["meet_media"]["chat_reply"]["intent_id"]
                == admission.reservation.intent_id
            )
            assert len(base64.b64decode(reply["media"]["video"]["base64"])) > 1000
    finally:
        engine.dispose()
