"""Actual signed Hub/Worker HTTP; explicit synthetic authority, no live service."""

import os
from unittest.mock import Mock

import pytest
from werkzeug.serving import make_server

from agent.models.persona_generation import PersonaGenerationRequest
from agent.services.persona_generated_assets import PersonaGeneratedAssets
from agent.services.persona_generation_transport import HttpPersonaGenerationWorker
from ananta_contracts.persona_generation import GenerationWire
from tests import test_persona_generation_tasks as task_cases
from tests.persona_generated_asset_fixture import inspection_target
from tests.test_persona_video_http import KEY, SilentHandler, serving
from worker.meet_media.persona_generation_executor import PersonaGenerationExecutor
from worker.meet_media.persona_generation_frames import render
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard

runtime = task_cases.runtime
generation_task = task_cases.generation_task
pytestmark = pytest.mark.timeout(60)


def authenticate(patch):
    patch.setattr(
        "agent.auth._validate_user_jwt",
        lambda token: {
            "sub": "actor",
            "tenant_id": "tenant",
            "project_id": "project",
            "role": "user",
        }
        if token == "synthetic-generation"
        else None,
    )
    patch.setattr("agent.auth._user_token_allows_current_request", lambda _: True)
    return {"Authorization": "Bearer synthetic-generation"}


@pytest.mark.parametrize("mode", ["success", "revoke"])
def test_real_signed_generation_http_rechecks_current_hub_authority(generation_task, app, tmp_path, mode):
    c = generation_task
    app.config["ROLE"] = "hub"
    app.extensions.update(persona_generation_worker_key=KEY, persona_generation_leases=c.leases)

    class Generator:
        def __init__(self, *, require_current, deadline_monotonic):
            self.require = require_current

        def generate(self, recipe):
            self.require()
            if mode == "revoke":
                c.access.require.side_effect = PermissionError("revoked")
            self.require()
            return render(recipe)

    with serving(make_server("127.0.0.1", 0, app, threaded=True, request_handler=SilentHandler)) as hub:
        executor = PersonaGenerationExecutor(
            tmp_path / "http-replay.db",
            generator=Generator,
            guard_factory=lambda assignment: PersonaLeaseGuard(
                f"http://127.0.0.1:{hub.server_port}/api/persona-media/v1/internal/generation-lease",
                KEY,
                assignment,
                kind="generation",
            ),
        )
        with serving(create_inspection_server(("127.0.0.1", 0), KEY, executor, wire=GenerationWire())) as server:
            c.service.worker = HttpPersonaGenerationWorker(
                f"http://private-worker.test:{server.server_port}{GenerationWire.path}",
                KEY,
                resolve_address=lambda *_: "127.0.0.1",
            )
            if mode == "revoke":
                with pytest.raises(ValueError):
                    task_cases.generate(c)
                assert task_cases.runs(c)[0]["state"] == "failed"
            else:
                result = task_cases.generate(c)
                assert result.content == render(c.request.recipe.model_dump())
                assert task_cases.runs(c)[0]["state"] == "succeeded"


@pytest.mark.parametrize("publish", [False, True])
def test_explicit_asset_composition_uses_separate_inspection_and_scoped_publish(
    generation_task, app, tmp_path, publish
):
    from worker.meet_media.persona_image import sanitize_image

    c = generation_task
    target = inspection_target(c.base, "image", tmp_path / "generated-assets", access=c.access, worker=Mock())
    target.tasks.worker.execute.side_effect = lambda assignment, content, media_type: sanitize_image(
        content, media_type
    )
    creator = PersonaGeneratedAssets(generator=c.service, image_assets=target.assets, image_policy=target.policy)
    request = PersonaGenerationRequest.model_validate(c.request.model_dump() | {"publish": publish})
    asset = creator.create(c.principal, "project", request)
    assert asset.image.classification == "test_only"
    assert len(task_cases.runs(c)) == 2
    assert {run["state"] for run in task_cases.runs(c)} == {"succeeded"}
    assert len({run["task_id"] for run in task_cases.runs(c)}) == 2
    assert target.assets.read_image(c.principal, "project", asset.image.artifact_id)
    if publish:
        assert target.assets.read_image(c.principal, "project", asset.image.artifact_id, purpose="publish")
    else:
        with pytest.raises(PermissionError):
            target.assets.read_image(c.principal, "project", asset.image.artifact_id, purpose="publish")
    target.policy.revoke_policy(c.principal, "project", asset.origin_binding, expected_revision=1)
    with pytest.raises((ValueError, PermissionError)):
        target.assets.read_image(c.principal, "project", asset.image.artifact_id)


def test_disabled_target_does_not_generate_or_issue_facts(generation_task):
    c = generation_task
    with pytest.raises(ValueError, match="disabled"):
        PersonaGeneratedAssets(generator=c.service).create(c.principal, "project", c.request)
    assert task_cases.runs(c) == []
    c.worker.execute.assert_not_called()


@pytest.mark.parametrize("token", [None, "worker", "service"])
def test_generation_creation_api_never_accepts_machine_auth(generation_task, app, monkeypatch, token):
    authenticate(monkeypatch)
    app.config["ROLE"] = "hub"
    creator = app.extensions["persona_generated_assets"] = Mock()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = app.test_client().post(
        "/api/persona-media/v1/projects/project/generated-assets",
        headers=headers,
        json={"request": generation_task.request.model_dump(mode="json")},
    )
    assert response.status_code == 401
    creator.create.assert_not_called()


@pytest.mark.skipif(os.environ.get("PERSONA_GENERATION_NATIVE_GATE") != "1", reason="explicit native codec gate")
def test_native_generator_and_inspection_ports_remain_separately_bound(generation_task, app, tmp_path):
    from tests.persona_native_codec_runner import NativePersonaCodecRunner
    from worker.meet_media.persona_generator import ProceduralPersonaGenerator
    from worker.meet_media.persona_video_inspector import PersonaVideoInspector

    c = generation_task
    runner = NativePersonaCodecRunner()
    c.request = PersonaGenerationRequest.model_validate(
        c.request.model_dump()
        | {
            "recipe": {"profile": "procedural-avatar-v1", "media_kind": "video", "palette": "teal"},
        }
    )
    target = inspection_target(c.base, "video", tmp_path / "native-assets", access=c.access, worker=Mock())
    import time

    def generate(assignment, recipe):
        c.leases.require(assignment)
        return ProceduralPersonaGenerator(
            require_current=lambda: c.leases.require(assignment),
            deadline_monotonic=time.monotonic() + assignment["deadline"] - time.time(),
            runner=runner,
        ).generate(recipe)

    def inspect(assignment, content, media_type):
        target.leases.require(assignment)
        return PersonaVideoInspector(
            require_current=lambda: target.leases.require(assignment),
            deadline_monotonic=time.monotonic() + assignment["deadline"] - time.time(),
            runner=runner,
        ).inspect(content, media_type)

    c.service.worker.execute.side_effect = generate
    target.tasks.worker.execute.side_effect = inspect
    asset = PersonaGeneratedAssets(generator=c.service, video_assets=target.assets, video_policy=target.policy).create(
        c.principal,
        "project",
        c.request,
    )
    assert asset.frames == 24
    assert len(task_cases.runs(c)) == 2
    assert target.assets.read_video(c.principal, "project", asset.video.artifact_id)
