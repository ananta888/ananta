"""Opt-in RTX turn with real profile SQL and HTTP lease; synthetic asset policy."""

import base64
import os
import threading

import pytest
from flask import Flask
from flask import request as flask_request
from werkzeug.serving import make_server

from agent.routes.meet import meet_bp
from agent.services.meet_media_transport import HttpMediaWorker
from agent.services.meet_turn_service import HubMediaTasks
from agent.services.repository_registry import get_repository_registry
from tests.test_meet_persona_profiles import bound as bound
from tests.test_meet_persona_profiles import selection, turn_service
from tests.test_persona_assets import setup as setup
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_profile_service import system as system
from worker.meet_media.contract import load_key


@pytest.mark.skipif(
    os.environ.get("MEET_PROFILE_GPU_GATE") != "1", reason="opt-in provisioned RTX worker and private Hub callback"
)
@pytest.mark.timeout(150)
def test_gpu_persona_turn_uses_real_profile_task_and_request_bound_hub_lease(app, request):
    request.getfixturevalue("runtime")
    fixture = request.getfixturevalue("bound")
    service, _, _ = turn_service(fixture, HubMediaTasks())
    key = load_key(os.environ["MEET_MEDIA_GPU_KEY_FILE"])
    service.worker = HttpMediaWorker(os.environ["MEET_MEDIA_GPU_ENDPOINT"], key)
    callback = Flask("meet-profile-gpu-callback")
    callback.config.update(ROLE="hub", TESTING=True)
    callback.register_blueprint(meet_bp)
    callback.before_request(
        lambda: None
        if (flask_request.method == "POST" and flask_request.path == "/api/meet/v1/internal/lease")
        else ("", 404)
    )
    callback.extensions.update(
        meet_binding_service=service.binding,
        meet_turn_service=service,
        meet_media_worker_key=key,
        repository_registry=get_repository_registry(app),
    )
    # Bind only the explicitly supplied private Docker gateway interface, not
    # the public Hub. A busy/missing endpoint is a bounded failure, not a prompt.
    host = os.environ["MEET_PROFILE_GPU_CALLBACK_HOST"]
    port = int(os.environ["MEET_PROFILE_GPU_CALLBACK_PORT"])
    import ipaddress

    if not ipaddress.ip_address(host).is_private or host in {"0.0.0.0", "::"}:
        raise ValueError("private_callback_interface_required")
    server = make_server(host, port, callback, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = service.execute(
            fixture.principal,
            "project",
            {
                "text": "Antworte ausschließlich mit dem einen Wort: Hallo.",
                "persona_profile": selection(fixture),
            },
        )
        task = get_repository_registry(app).task_repo.get_by_id(result["task_id"])
        assert task.status == "completed"
        assert task.worker_execution_context["meet_media"]["persona_profile"] == selection(fixture)
        assert result["persona_image"] == fixture.asset.image.model_dump(mode="json")
        assert result["engines"]["speech"] == "piper-cuda"
        assert result["engines"]["video"] == "persona-image-h264_nvenc"
        assert base64.b64decode(result["audio"]["base64"])[8:12] == b"WAVE"
        assert len(base64.b64decode(result["video"]["base64"])) > 1000
        assert "meeting" not in result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()
