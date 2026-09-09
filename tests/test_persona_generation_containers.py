"""Actual two-container generated asset chain; synthetic policy, no production claim."""

import os

import pytest
from werkzeug.serving import make_server

from agent.models.persona_assets import PersonaImageAsset
from agent.models.persona_generation import PersonaGenerationRequest
from agent.models.persona_video_assets import PersonaVideoAsset
from agent.services.persona_generated_assets import PersonaGeneratedAssets
from agent.services.persona_generation_transport import HttpPersonaGenerationWorker
from agent.services.persona_image_transport import HttpPersonaImageWorker
from agent.services.persona_video_transport import HttpPersonaVideoWorker
from ananta_contracts.persona_generation import GenerationWire
from tests import test_persona_generation_tasks as task_cases
from tests.persona_generated_asset_fixture import inspection_target
from tests.persona_generation_containers import PersonaGenerationContainers
from tests.test_persona_generation_http import authenticate
from tests.test_persona_video_http import SilentHandler, serving

runtime = task_cases.runtime
generation_task = task_cases.generation_task
pytestmark = [
    pytest.mark.skipif(
        os.environ.get("PERSONA_GENERATION_CONTAINER_GATE") != "1", reason="explicit private container gate"
    ),
    pytest.mark.timeout(150),
]
GEN_KEY = b"synthetic-generator-container-key-0001"
INSPECT_KEY = b"synthetic-inspection-container-key-0002"


@pytest.mark.parametrize("kind", ["image", "video"])
def test_packaged_generation_and_separate_inspection_admit_and_revoke_owned_asset(
    generation_task,
    app,
    tmp_path,
    monkeypatch,
    record_property,
    kind,
):
    c = generation_task
    app.config["ROLE"] = "hub"
    access = app.extensions["project_access_authority"]
    c.policy.authority.access = access
    c.service.admission.authority.access = access
    app.extensions.update(persona_generation_worker_key=GEN_KEY, persona_generation_leases=c.leases)
    c.request = PersonaGenerationRequest.model_validate(
        c.request.model_dump()
        | {
            "recipe": {"profile": "procedural-avatar-v1", "media_kind": kind, "palette": "teal"},
        }
    )
    headers = authenticate(monkeypatch)
    image = os.environ["PERSONA_GENERATION_IMAGE"]
    with PersonaGenerationContainers(image) as containers:
        with serving(make_server(containers.gateway, 0, app, threaded=True, request_handler=SilentHandler)) as hub:
            callback = f"http://{containers.gateway}:{hub.server_port}/api/persona-media/v1/internal"
            generator_id, address, port = containers.start(
                "generation", tmp_path / "generator", callback + "/generation-lease", GEN_KEY
            )
            c.service.worker = HttpPersonaGenerationWorker(f"http://{address}:{port}{GenerationWire.path}", GEN_KEY)
            inspector_id, address, port = containers.start(
                kind, tmp_path / "inspector", callback + f"/{kind}-lease", INSPECT_KEY
            )
            worker = (
                HttpPersonaImageWorker(f"http://{address}:{port}/v1/persona-images", INSPECT_KEY)
                if kind == "image"
                else HttpPersonaVideoWorker(f"http://{address}:{port}/v1/persona-videos", INSPECT_KEY)
            )
            target = inspection_target(c.base, kind, tmp_path / "private-assets", access=access, worker=worker)
            app.extensions[f"persona_{kind}_worker_key"] = INSPECT_KEY
            app.extensions[f"persona_{kind}_leases"] = target.leases
            app.extensions["persona_assets" if kind == "image" else "persona_video_assets"] = target.assets
            creator = PersonaGeneratedAssets(
                generator=c.service,
                **{f"{kind}_assets": target.assets, f"{kind}_policy": target.policy},
            )
            app.extensions["persona_generated_assets"] = creator
            http = app.test_client()
            path = "/api/persona-media/v1/projects/project"
            response = http.post(
                path + "/generated-assets", headers=headers, json={"request": c.request.model_dump(mode="json")}
            )
            assert response.status_code == 201, response.json
            assert response.headers["Cache-Control"] == "no-store"
            asset = (PersonaImageAsset if kind == "image" else PersonaVideoAsset).model_validate(response.json["asset"])
            reference = getattr(asset, kind)
            assert reference.classification == "test_only"
            if kind == "video":
                assert asset.frames == 24
            runs = task_cases.runs(c)
            assert len(runs) == 2 and {run["state"] for run in runs} == {"succeeded"}
            assert len({run["assignment_id"] for run in runs}) == 2
            assert {c.state.get(run["task_id"]).task_kind for run in runs} == {
                "persona_media_generation",
                f"persona_{kind}_inspection",
            }
            assert all(run["synthetic"] and run["evidence_scope"] == "test" for run in runs)
            asset_path = path + f"/{kind}s/{reference.artifact_id}"
            preview = http.get(asset_path + "/preview", headers=headers)
            assert preview.status_code == 200 and preview.data.startswith(b"\x89PNG")
            with pytest.raises(PermissionError):
                getattr(target.assets, f"read_{kind}")(c.principal, "project", reference.artifact_id, purpose="publish")
            assert http.delete(asset_path, headers=headers, json={"expected_revision": 2}).json["state"] == "revoked"
            assert http.get(asset_path + "/preview", headers=headers).status_code == 409
            assert generator_id != inspector_id
            record_property(
                "persona_generation",
                {
                    "kind": kind,
                    "image": image,
                    "separate_workers": 2,
                    "successful_hub_runs": 2,
                    "preview_bytes": len(preview.data),
                    "frames": asset.frames if kind == "video" else 1,
                    "synthetic_policy": True,
                    "production_release_evidence": False,
                    "human_capture": False,
                },
            )
