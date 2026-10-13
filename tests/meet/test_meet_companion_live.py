"""Private actual legacy run acceptance, not productive policy/release evidence."""

import base64
import hashlib
import json
import os
import selectors
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from flask import Flask
from werkzeug.serving import WSGIRequestHandler, make_server

from agent.routes.meet import meet_bp
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from agent.services.meet_turn_service import MeetTurnService
from tests.meet_companion_build import require_current_browser_build
from tests.meet_dialog_browser_fixture import docker
from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
from tests.test_meet_dialog_cross_repository import close_bridge


@pytest.mark.timeout(240)
@pytest.mark.skipif(os.environ.get("MEET_COMPANION_LIVE_GATE") != "1", reason="explicit private legacy loop gate")
@pytest.mark.parametrize("scenario", ["backlog", "hub-revoke", "pressure", "hub-recovery", "source-revoke",
                                     "assigned", "assigned-revoke"])
def test_actual_packaged_companion_answers_backlog_across_long_model_and_renewals(tmp_path, scenario):
    root = Path(__file__).resolve().parents[2]
    meet = root.parent / "webrtc-minimize-server"
    require_current_browser_build(meet, os.environ.get("MEET_TEST_PUBLIC_DIR"))
    image = os.environ["MEET_COMPANION_PACKAGED_IMAGE"]
    key = Ed25519PrivateKey.generate()
    private, public, worker_key = (tmp_path / name for name in ("hub.pem", "public.pem", "worker-key"))
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    hmac_key = b"synthetic-legacy-companion-private-key"
    worker_key.write_bytes(hmac_key)
    worker_key.chmod(0o644)  # Synthetic, ephemeral, readable by the nonroot test container only.
    tmp_path.chmod(0o755)
    name = "ananta-companion-live-" + uuid4().hex[:12]
    with ExitStack() as cleanup:
        env = os.environ | {"ANANTA_TEST_MEET_REPOSITORY": str(meet), "MEET_TEST_HUB_PUBLIC_KEY": str(public)}
        bridge = subprocess.Popen(["node", str(Path(__file__).with_name("meet_companion_live_bridge.mjs"))],
                                  env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        cleanup.callback(close_bridge, bridge)
        worker_created = False

        def failure_observation():
            if not worker_created:
                return {}
            state = json.loads(docker("inspect", "-f", "{{json .State}}", name))
            logs = docker("logs", name)
            result = {"worker_running": state["Running"], "worker_exit": state["ExitCode"],
                    "hub_grants": len(grants), "hub_denials": len(denials),
                    "model_stop_receipt": '"owned_model_process_stopped"' in logs}
            if logs.splitlines():
                try:
                    receipt = json.loads(logs.splitlines()[-1])
                except ValueError:
                    receipt = {}
                code = receipt.get("assigned_failure")
                if code in {"meet_client_probe_invalid", "meet_dialog_session_checkpoint_required",
                            "meet_dialog_session_operation_failed", "meet_browser_network_origin_invalid",
                            "assigned_runtime_setup_failed", "meet_client_probe_unsupported",
                            "meet_machine_navigation_denied", "meet_dialog_session_expired"}:
                    result["assigned_failure"] = code
                    if receipt.get("phase") in {'browser_setup', 'ready', 'ready_done', 'join', 'joined',
                                                'grant_read', 'grant_read_done'}:
                        result["assigned_phase"] = receipt["phase"]
                    if receipt.get("kind") in {'Error', 'TimeoutError', 'ValueError', 'KeyError', 'TypeError', 'other'}:
                        result["assigned_error_kind"] = receipt["kind"]
            return result

        def receive(timeout):
            with selectors.DefaultSelector() as selector:
                selector.register(bridge.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + timeout
                while not selector.select(min(1, max(0, deadline - time.monotonic()))):
                    assert time.monotonic() < deadline, "bounded legacy bridge observation timeout"
                    if scenario == "assigned" and worker_created:
                        observation = failure_observation()
                        assert observation["worker_running"], json.dumps(observation)
                line = bridge.stdout.readline(8193)
            assert line and len(line) <= 8192
            value = json.loads(line)
            assert "error" not in value, json.dumps(value | failure_observation())
            return value

        ready = receive(60)
        network = json.loads(docker("network", "inspect", ready["network"]))[0]
        assert network["Internal"] is True
        gateway = network["IPAM"]["Config"][0]["Gateway"]
        principal = MeetTurnService._companion("synthetic", "synthetic")
        binding = SyntheticMeetBinding(ready["origin"], ready["room_id"], principal)
        issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", private)
        service = MeetTurnService(binding, None, None, {("synthetic", "synthetic")}, grant_issuer=issuer)
        app = Flask(__name__)
        app.config.update(TESTING=True, ROLE="hub")
        app.register_blueprint(meet_bp)
        app.extensions.update(meet_binding_service=binding, meet_turn_service=service, meet_media_worker_key=hmac_key)
        task_fixture = None
        if scenario.startswith("assigned"):
            from agent.services.meet_companion_task_grants import MeetCompanionTaskGrants
            from agent.services.meet_contract import MeetProfile
            from agent.services.meet_dialog_controls import initial_controls
            from tests.test_meet_dialog_authority import fixture

            task_fixture = fixture()
            caps = ["avatar.publish", "chat.read", "chat.send", "speech.publish"]
            task_fixture.context.update(capabilities=caps, companion_execution=True, room_id=ready["room_id"],
                                        chat_mode="room")
            task_fixture.context["controls"] = initial_controls(caps, "room", "off", task_fixture.now * 1000)
            for source in ("avatar", "speech"):
                task_fixture.context["controls"][source]["enabled"] = True
            task_fixture.binding.profile = MeetProfile(ready["origin"])
            task_fixture.binding.read.return_value = {
                "invite_url": task_fixture.binding.profile.invite(ready["room_id"]),
            }
            task_fixture.authority.policies[("tenant", "project")] = frozenset(caps)
            app.extensions["meet_companion_task_grants"] = MeetCompanionTaskGrants(task_fixture.authority, issuer)
            task_assignment = {"schema": "ananta.meet-dialog-assignment.v1", "task_id": "task", "lease_id": "dispatch",
                "runtime_id": "runtime", "session_id": "hub-session", "tenant_id": "tenant", "project_id": "project",
                "deadline": task_fixture.context["deadline"], "capabilities": caps, "audio_mode": "off",
                "companion_execution": True,
                "meeting": issuer.issue_dialog(task_fixture.authority, "task", "dispatch", "runtime", time.time())}
            assignment_file = tmp_path / "assignment.json"
            assignment_file.write_text(json.dumps(task_assignment))
            assignment_file.chmod(0o644)  # Synthetic grant, private test mount only.
        grants = []
        denials = []
        legacy_calls = []

        @app.after_request
        def count_grants(response):
            from flask import request
            paths = {"/api/meet/v1/internal/companion", "/api/meet/v1/internal/companion/task-grant"}
            if request.path == "/api/meet/v1/internal/companion":
                legacy_calls.append(response.status_code)
            if request.path in paths and response.status_code == 200:
                grants.append(1)
            if request.path in paths and response.status_code == 403:
                denials.append(1)
            return response

        class Quiet(WSGIRequestHandler):
            def log(self, *_args, **_kwargs):
                pass

        hub = make_server(gateway, 0, app, threaded=True, request_handler=Quiet)
        cleanup.callback(hub.server_close)
        cleanup.callback(hub.shutdown)
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        certificate = x509.load_pem_x509_certificate(Path(ready["certificate"]).read_bytes())
        spki = base64.b64encode(hashlib.sha256(certificate.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
        command = ["create", "--name", name, "--network", ready["network"], "--init", "--user", "11111:11111",
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                   "--security-opt", "seccomp=" + str(root / "docker/meet-media/chromium-seccomp.json"),
                   "--memory", "2g", "--cpus", "2", "--pids-limit", "256", "--shm-size", "256m",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                   "--tmpfs", "/state:rw,nosuid,nodev,size=64m,uid=11111,gid=11111,mode=0700"]
        for source, target in [(worker_key, "/test/key"), (Path(ready["certificate"]), "/test/ca.pem"),
                               (Path(__file__).with_name("meet_companion_live_probe.py"), "/test/probe.py")]:
            command += ["--mount", f"type=bind,src={source},dst={target},readonly"]
        values = {"MEET_ORIGIN": ready["origin"], "MEET_COMPANION_PROJECT": "synthetic",
                  "MEET_HUB_COMPANION_URL": f"http://{gateway}:{hub.server_port}/api/meet/v1/internal/companion",
                  "MEET_WORKER_KEY_FILE": "/test/key", "SSL_CERT_FILE": "/test/ca.pem",
                  "NODE_EXTRA_CA_CERTS": "/test/ca.pem", "TEST_CERT_SPKI": spki,
                  "MEET_COMPANION_RENEW": "10", "MEET_COMPANION_PUBLIC_ROOM_CHECK": "0",
                  "ANANTA_CPU_FALLBACK": "1", "MEET_AVATAR_SERVICE_ENABLED": "0",
                  "MEET_AVATAR_IDLE_CLIP": "/state/missing.mp4", "PYTHONPATH": "/app"}
        if scenario.startswith("assigned"):
            command += ["--mount", f"type=bind,src={assignment_file},dst=/test/assignment,readonly"]
            values.update(TEST_ASSIGNED_COMPANION="1",
                          MEET_HUB_COMPANION_TASK_URL=f"http://{gateway}:{hub.server_port}/api/meet/v1/internal/companion/task-grant")
        if scenario == "source-revoke":
            values["TEST_SOURCE_WITHDRAWAL"] = "1"
        for field, value in values.items():
            command += ["-e", field + "=" + value]
        cleanup.callback(docker, "rm", "-f", name)
        docker(*command, "--entrypoint", "python", image, "/test/probe.py")
        docker("start", name)

        worker_created = True

        bridge_command = {"hub-recovery": "hub-revoke", "assigned": "backlog",
                          "assigned-revoke": "hub-revoke"}.get(scenario, scenario)
        bridge.stdin.write(bridge_command + "\n")
        bridge.stdin.flush()
        if scenario in {"hub-revoke", "hub-recovery", "source-revoke", "assigned-revoke"}:
            assert receive(60) == {"inputs_sent": 3}
            started = time.monotonic()
            while True:
                received = docker("exec", name, "python", "-c",
                                  "from pathlib import Path;print(Path('/state/synthetic-model-started').exists())")
                if received == "True":
                    break
                assert time.monotonic() - started < 10, "bounded model-start observation missing"
                time.sleep(0.1)
            if scenario == "source-revoke":
                bridge.stdin.write("source-depart\n")
                bridge.stdin.flush()
                assert receive(30) == {"source_departed": True, "machine_departed": True}
                assert not denials and time.monotonic() - started < 30
                assert docker("wait", name) == "0"
                assert json.loads(docker("logs", name).splitlines()[-1]) == {
                    "owned_model_process_stopped": True, "recv_count": 1, "reply_count": 0}
                return
            if scenario == "assigned-revoke":
                task_fixture.task.status = "cancelled"
            else:
                service.allowed_scopes = frozenset()  # Withdraw actual Hub-owned preauthorization.
            assert receive(30) == {"revoked": True, "replies": 0, "camera_absent": True}
            assert denials and time.monotonic() - started < 30
            assert docker("wait", name) == "0"
            assert json.loads(docker("logs", name).splitlines()[-1]) == {"owned_model_process_stopped": True}
            if scenario in {"hub-revoke", "assigned-revoke"}:
                if scenario == "assigned-revoke":
                    assert not legacy_calls
                return
            # Explicit test-owned restart after the Hub restores policy; no
            # Worker retry may restore an authority which the Hub withdrew.
            service.allowed_scopes = frozenset({("synthetic", "synthetic")})
            grants.clear()
            docker("start", name)
            bridge.stdin.write("recovery\n")
            bridge.stdin.flush()
        result = receive(180)
        audio_windows = result.pop("audio_windows")
        assert type(audio_windows) is int and audio_windows > 0
        count = 32 if scenario == "pressure" else 3
        assert result == {"replies": count, "unique": count, "captures": 0, "transform_errors": 0,
                          "proxy_drops": 0, "decoded_avatar": True}
        assert len(grants) >= 3
        if scenario == "assigned":
            assert not denials and not legacy_calls
            task_fixture.task.status = "cancelled"
            assert docker("wait", name) == "0"
            assert json.loads(docker("logs", name).splitlines()[-1]) == {"owned_model_process_stopped": True}
            assert denials
            return
        # Export counts of closed diagnostic markers, never message IDs/text,
        # authority bindings, browser content or arbitrary exception payloads.
        diagnostic = json.loads(docker("exec", name, "python", "-c",
            "from pathlib import Path;import json;s=Path('/state/companion.log').read_text();"
            "print(json.dumps({k:s.count(k) for k in ['RECV','REPLY','chat_gap',"
            "'chat_control_err','chat_reply_err','RENEW status=200']}))"))
        assert diagnostic["RECV"] == diagnostic["REPLY"] == count
        assert diagnostic["chat_gap"] == diagnostic["chat_control_err"] == diagnostic["chat_reply_err"] == 0
        assert diagnostic["RENEW status=200"] >= 2
        docker("exec", name, "touch", "/state/companion-stop")
        assert docker("wait", name) == "0"
        assert json.loads(docker("logs", name).splitlines()[-1]) == {"owned_model_process_stopped": True}
