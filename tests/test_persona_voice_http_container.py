"""Actual isolated CPU descriptor worker and Hub task; explicit test-only evidence."""

import os

import pytest
from werkzeug.serving import make_server

from agent.services.persona_voice_transport import create_voice_worker_transport
from tests.persona_voice_container_fixture import descriptor_worker, private_voice_network
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_http import SilentHandler, serving
from tests.test_persona_voice_asset_service import admit
from tests.test_persona_voice_asset_service import voice_assets as voice_assets
from tests.test_persona_voice_http import KEY
from tests.test_persona_voice_tasks import voice_task as voice_task

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("PERSONA_VOICE_HTTP_CONTAINER_GATE") != "1", reason="opt-in private CPU Docker gate"
    ),
    pytest.mark.timeout(120),
]


def test_private_descriptor_container_completes_only_current_hub_voice_run(request, app, tmp_path, record_property):
    fixture = request.getfixturevalue("voice_assets")
    case = fixture.case
    case.policy.access = app.extensions["project_access_authority"]
    app.config["ROLE"] = "hub"
    app.extensions["persona_voice_worker_key"] = KEY
    app.extensions["persona_voice_leases"] = case.leases
    with private_voice_network() as (network, gateway):
        with serving(make_server(gateway, 0, app, threaded=True, request_handler=SilentHandler)) as hub:
            callback = f"http://{gateway}:{hub.server_port}/api/persona-media/v1/internal/voice-lease"
            with descriptor_worker(tmp_path, network, callback, KEY) as (address, image):
                case.tasks.worker = create_voice_worker_transport(f"http://{address}:8097/v1/persona-voices", KEY)
                asset = admit(fixture)
                case.receipts.require_asset(case.principal, asset)
                assert fixture.service.read(case.principal, "project", asset.voice.artifact_id) == case.content
                task = case.state.get(asset.inspection.task_id)
                assert task.status == "completed" and task.task_kind == "persona_voice_inspection"
                run = case.base.repository.get_run(
                    tenant_id="tenant", project_id="project", run_id=asset.inspection.run_id
                )
                assert run.state == "succeeded" and run.synthetic and run.evidence_scope == "test"
                case.policy.revoke_policy(
                    case.principal, "project", case.permission.source.source_id, expected_revision=1
                )
                with pytest.raises(ValueError):
                    fixture.service.read(case.principal, "project", asset.voice.artifact_id, purpose="publish")
                record_property(
                    "private_voice_descriptor",
                    {
                        "status": "passed",
                        "image": image,
                        "hub_task_completed": True,
                        "actual_cpu_descriptor_inspection": True,
                        "real_model_synthesis": False,
                        "classification": "test_only",
                        "production_release_evidence": False,
                        "human_capture_used": False,
                        "public_service_modified": False,
                    },
                )
