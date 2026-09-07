"""Opt-in, disposable CPU container + real Hub run; synthetic input, no live Meet."""

import base64
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager

import pytest
from werkzeug.serving import make_server

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_video_assets import PersonaVideoAsset
from agent.repositories.persona_video_assets import create_video_asset_catalog
from agent.repositories.persona_video_retention import create_video_retention_store
from agent.services.artifact_store import ArtifactStore
from agent.services.persona_retention_runner import PersonaRetentionRunner
from agent.services.persona_retention_service import PersonaRetentionService
from agent.services.persona_retention_tasks import HubPersonaRetentionTasks
from agent.services.persona_video_asset_service import PersonaVideoAssetService
from agent.services.persona_video_erasure import create_video_erasure_service
from agent.services.persona_video_storage import PersonaVideoStorage
from agent.services.persona_video_transport import HttpPersonaVideoWorker
from ananta_contracts.persona_video import decode_video
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_http import KEY, SilentHandler, serving
from tests.test_persona_video_tasks import execute
from tests.test_persona_video_tasks import video_task as video_task

pytestmark = [
    pytest.mark.skipif(os.environ.get("PERSONA_VIDEO_HTTP_CONTAINER_GATE") != "1", reason="opt-in CPU Docker gate"),
    pytest.mark.timeout(150),
]


def docker(*args, timeout=15):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, f"Disposable video gate Docker command failed: {result.stderr[-2000:]}"
    return result.stdout.strip()


@contextmanager
def private_network():
    name = "persona-video-test-" + uuid.uuid4().hex
    docker("network", "create", "--internal", name)
    try:
        info = json.loads(docker("network", "inspect", name))[0]
        assert info["Internal"] is True
        yield name, info["IPAM"]["Config"][0]["Gateway"]
    finally:
        docker("network", "rm", name)


@contextmanager
def cpu_worker(tmp_path, network, callback):
    name = "persona-video-test-" + uuid.uuid4().hex
    key = tmp_path / "worker-key"
    key.write_bytes(KEY)
    key.chmod(0o600)
    state = tmp_path / "worker-state"
    state.mkdir(mode=0o700)
    created = False
    try:
        docker(
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--network",
            network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--init",
            "--memory",
            "384m",
            "--cpus",
            "1",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "--mount",
            f"type=bind,source={key},target=/run/secrets/persona-video-key,readonly",
            "--mount",
            f"type=bind,source={state},target=/state",
            "-e",
            "PERSONA_VIDEO_WORKER_KEY_FILE=/run/secrets/persona-video-key",
            "-e",
            f"PERSONA_VIDEO_HUB_LEASE_URL={callback}",
            "ananta-persona-video:local",
        )
        created = True
        # Slow Docker startup is separate from the twenty-second assignment
        # deadline, which begins only after fixture setup and source admission.
        docker("start", name, timeout=45)
        info = json.loads(docker("inspect", name))[0]
        assert not info["HostConfig"]["PortBindings"] and not info["HostConfig"]["DeviceRequests"]
        assert info["HostConfig"]["ReadonlyRootfs"] and info["HostConfig"]["CapDrop"] == ["ALL"]
        address = info["NetworkSettings"]["Networks"][network]["IPAddress"]
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection((address, 8096), timeout=0.25):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable CPU worker did not become ready within five seconds")
                threading.Event().wait(0.05)
        yield name, address
    finally:
        if created:
            docker("rm", "--force", name, timeout=30)


def admit_synthetic_source(case, content):
    digest = hashlib.sha256(content).hexdigest()
    source = case.base.registry.register_source(
        tenant_id="tenant",
        project_id="project",
        origin_type="persona_video",
        origin_digest=digest,
        content_digest=digest,
        policy_digest="a" * 64,
        evidence_scope="test",
        synthetic=True,
    )
    permission = case.permission.model_copy(
        update={
            "source": PersonaSourcePin(source_id=source.source_id, binding_digest=source.binding_digest),
            "policy_binding": "video-http-container-policy",
        }
    )
    case.policy.install(case.principal, permission, expected_revision=0)
    case.admission = case.policy.admit(
        case.principal,
        "project",
        digest,
        origin_binding=source.source_id,
        license_binding=permission.license.source_id,
        consent_binding=None,
    )
    case.content = content
    case.permission = permission


def assert_video_asset_lifecycle(case, tmp_path, expected, app, monkeypatch, retire_mode):
    for model in (ArtifactDB, ArtifactVersionDB):
        model.__table__.create(case.base.engine)
    catalog = create_video_asset_catalog(case.base.engine)
    catalog.initialize()
    storage = PersonaVideoStorage(ArtifactStore(tmp_path / "private-assets"))
    service = PersonaVideoAssetService(policy=case.policy, tasks=case.tasks, catalog=catalog, storage=storage)
    app.extensions["persona_video_assets"] = service
    erasure = create_video_erasure_service(policy=case.policy, catalog=catalog, base_dir=storage.store.base_dir)
    app.extensions["persona_video_erasure"] = erasure
    # Explicit headless JWT-validation fixture; actual project authority, source
    # policy, Hub task, Registry receipt and filesystem remain real.
    monkeypatch.setattr(
        "agent.auth._validate_user_jwt",
        lambda token: {
            "sub": "actor",
            "tenant_id": "tenant",
            "project_id": "project",
            "role": "user",
        }
        if token == "synthetic-video-api"
        else None,
    )
    monkeypatch.setattr("agent.auth._user_token_allows_current_request", lambda _: True)
    http = app.test_client()
    headers = {"Authorization": "Bearer synthetic-video-api"}
    path = "/api/persona-media/v1/projects/project/videos"
    response = http.post(
        path,
        headers=headers,
        json={
            "content": base64.b64encode(case.content).decode(),
            "media_type": "video/mp4",
            "origin_binding": case.permission.source.source_id,
            "license_binding": case.permission.license.source_id,
            "consent_binding": None,
        },
    )
    assert response.status_code == 201 and response.json["revision"] == 2
    asset = PersonaVideoAsset.model_validate(response.json["asset"])
    path += "/" + asset.video.artifact_id
    case.receipts.require_asset(case.principal, asset)
    assert http.get(path + "/preview", headers=headers).data == expected.preview
    assert service.read_video(case.principal, "project", asset.video.artifact_id, purpose="publish") == expected.video
    assert http.delete(path, headers=headers, json={"expected_revision": 2}).json == {"revision": 3, "state": "revoked"}
    assert http.get(path + "/preview", headers=headers).status_code == 409
    if retire_mode == "purge":
        assert http.post(path + "/purge", headers=headers, json={"expected_revision": 3}).json == {
            "revision": 5,
            "state": "purged",
            "secure_device_erasure": False,
        }
    else:
        assert_video_retention(case, app, http, headers, path, catalog, erasure)
    assert not list(storage.store.base_dir.rglob("v0001__*"))
    assert catalog.get_retired("tenant", "project", asset.video.artifact_id) == (asset, 5, "purged")


def assert_video_retention(case, app, http, headers, path, catalog, erasure):
    store = create_video_retention_store(case.base.engine)
    store.initialize()
    now = [time.time()]
    app.extensions["persona_video_retention"] = PersonaRetentionService(
        policy=case.policy,
        catalog=catalog,
        store=store,
        clock=lambda: now[0],
    )
    runner = PersonaRetentionRunner(
        policy=case.policy,
        catalog=catalog,
        store=store,
        erasure=erasure,
        tasks=HubPersonaRetentionTasks(kind="video", clock=lambda: now[0]),
        clock=lambda: now[0],
    )
    response = http.put(
        path + "/retention",
        headers=headers,
        json={
            "asset_revision": 3,
            "expected_revision": 0,
            "delete_after_seconds": 60,
        },
    )
    assert response.status_code == 200 and response.json["state"] == "scheduled"
    assert runner.run_once()["claimed"] == 0
    # Advance only the injected retention clock; no interactive wait or task
    # deadline extension is needed to test due scheduling and real file erasure.
    now[0] += 61
    assert runner.run_once()["completed"] == 1
    assert http.get(path + "/retention", headers=headers).json["state"] == "completed"


@pytest.mark.parametrize("retire_mode", ["purge", "retention"])
def test_real_cpu_video_decoder_over_private_http_completes_reserved_hub_run(
    request, app, tmp_path, monkeypatch, retire_mode
):
    case = request.getfixturevalue("video_task")
    case.policy.access = app.extensions["project_access_authority"]
    app.config["ROLE"] = "hub"
    app.extensions["persona_video_worker_key"] = KEY
    app.extensions["persona_video_leases"] = case.leases
    with private_network() as (network, gateway):
        with serving(make_server(gateway, 0, app, threaded=True, request_handler=SilentHandler)) as hub:
            callback = f"http://{gateway}:{hub.server_port}/api/persona-media/v1/internal/video-lease"
            with cpu_worker(tmp_path, network, callback) as (container, address):
                # Fixture generation is not a delegated production operation. It uses
                # FFmpeg testsrc2/sine, never human hardware or a remote asset URL.
                raw = docker(
                    "exec",
                    container,
                    "python",
                    "-c",
                    (
                        "import base64,json; "
                        "from worker.meet_media.persona_clip_smoke import synthetic_clip; "
                        "from ananta_contracts.persona_video import encode_video; "
                        "source,clip=synthetic_clip(); "
                        "print(json.dumps({'source':base64.b64encode(source).decode(),'clip':encode_video(clip)}))"
                    ),
                    timeout=25,
                )
                fixture = json.loads(raw)
                content = base64.b64decode(fixture["source"], validate=True)
                expected = decode_video(fixture["clip"], hashlib.sha256(content).hexdigest())
                admit_synthetic_source(case, content)
                case.tasks.worker = HttpPersonaVideoWorker(f"http://{address}:8096/v1/persona-videos", KEY)
                result = execute(case)
                case.receipts.require_completed(case.principal, case.admission, result)
                assert result.video == expected and 2 <= result.video.frames <= 120
                assert case.state.get(result.task_id).status == "completed"
                run = case.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=result.run_id)
                assert run.state == "succeeded" and run.synthetic and run.evidence_scope == "test"
                assert_video_asset_lifecycle(case, tmp_path, expected, app, monkeypatch, retire_mode)
                assert not docker("exec", container, "sh", "-c", "command -v nvidia-smi || true")
