"""Owned private receive infrastructure; separate from media scenario assertions."""

import base64
import hashlib
import json
import os
import selectors
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace


@contextmanager
def packaged_receive(app, tmp_path, monkeypatch, *, source, image):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    from werkzeug.serving import WSGIRequestHandler, make_server

    from agent.database import engine
    from agent.repositories.meet_chat_dispatches import SqlChatDispatches
    from agent.repositories.meet_chat_reservations import SqlChatReservations
    from agent.services.meet_authorization_client import MeetAuthorizationClient
    from agent.services.meet_dialog_authority import MeetDialogAuthority
    from agent.services.meet_dialog_service import MeetDialogService
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer
    from agent.services.meet_media_transport import HttpMediaWorker
    from agent.services.source_control_access_policy import HubSourcePrincipal
    from tests.meet_companion_build import require_current_browser_build
    from tests.meet_dialog_browser_fixture import docker
    from tests.meet_dialog_cleanup import cancel_fixture_dialog
    from tests.meet_dialog_lifecycle_fixture import seed_parent
    from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
    from tests.meet_dialog_worker_container import DialogWorkerContainer
    from tests.test_meet_dialog_cross_repository import close_bridge

    if source not in {"camera", "screen", "microphone", "screen-audio"}:
        raise ValueError("test_receive_source_invalid")
    gpu = source in {"microphone", "screen-audio"}
    capability = "audio.receive" if gpu else "video.receive"
    bridge_kind = "audio" if gpu else "visual"
    assert os.environ.get("ANANTA_TEST_DATABASE_MODE") == "wal"
    meet = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    require_current_browser_build(meet, os.environ.get("MEET_TEST_PUBLIC_DIR"))
    key = Ed25519PrivateKey.generate()
    private, public, worker_key = (tmp_path / name for name in ("hub.pem", "hub-public.pem", "worker-key"))
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    hmac_key = b"synthetic-visual-test-worker-key-material"
    worker_key.write_bytes(hmac_key)
    worker_key.chmod(0o600)
    principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
    with ExitStack() as cleanup:
        bridge = subprocess.Popen(
            ["node", f"test/helpers/machine-{bridge_kind}-hub-bridge.mjs"],
            cwd=meet,
            env=os.environ
            | {
                "MEET_TEST_HUB_PUBLIC_KEY": str(public),
                "MEET_TEST_VISUAL_SOURCE": source,
                "MEET_TEST_AUDIO_SOURCE": source,
                "MEET_TEST_AUDIO_WAV": str(tmp_path / "synthetic.wav"),
            },
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        cleanup.callback(close_bridge, bridge)

        def receive(timeout=25):
            with selectors.DefaultSelector() as selector:
                selector.register(bridge.stdout, selectors.EVENT_READ)
                assert selector.select(timeout), "receive bridge bounded response missing"
                line = bridge.stdout.readline(4097)
            assert line and len(line) <= 4096, "receive bridge closed or oversized"
            response = json.loads(line)
            assert "bridge_error" not in response, response
            return response

        def command(name):
            assert name in ({"source", "grant", "revoke", "speak"} if gpu else {"source", "grant", "revoke"})
            bridge.stdin.write(name + "\n")
            bridge.stdin.flush()
            return receive()

        ready = receive(60)
        monkeypatch.setenv("SSL_CERT_FILE", ready["certificate"])
        certificate = x509.load_pem_x509_certificate(Path(ready["certificate"]).read_bytes())
        spki = base64.b64encode(
            hashlib.sha256(
                certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            ).digest()
        ).decode()
        network = json.loads(docker("network", "inspect", ready["test_network"]))[0]
        assert network["Internal"] is True and len(network["IPAM"]["Config"]) == 1
        gateway = network["IPAM"]["Config"][0]["Gateway"]

        class Quiet(WSGIRequestHandler):
            def log(self, *_args, **_kwargs):
                pass

        hub = make_server(gateway, 0, app, threaded=True, request_handler=Quiet)
        cleanup.callback(hub.server_close)
        cleanup.callback(hub.shutdown)
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        container = DialogWorkerContainer(
            ready["test_network"],
            image,
            f"http://{gateway}:{hub.server_port}/api/meet/v1/internal/dialog",
            lifetime=240,
            diagnostics=True,
            gpu=gpu,
        )
        cleanup.callback(container.close)
        container.start(worker_key, ready["certificate"], spki)
        seed_parent(engine, tenant="synthetic", project="synthetic", publisher=container.origin)
        binding = SyntheticMeetBinding(ready["origin"], ready["room_id"], principal)
        tasks = HubDialogTasks(publisher_url=container.origin)
        authority = MeetDialogAuthority(tasks, binding, {("synthetic", "synthetic"): [capability]})
        issuer = MeetMachineGrantIssuer("https://synthetic-hub.example.test", private)
        transport = HttpMediaWorker(container.origin + "/v1/turns", hmac_key)
        reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
        reservations.initialize()
        dispatches.initialize()
        service = MeetDialogService(
            authority,
            tasks,
            MeetAuthorizationClient(authority, issuer),
            issuer,
            MeetDialogWorkerRouter(authority, tasks, {container.origin: transport}, container.origin),
            transport,
            reservations,
            dispatches,
        )
        app.config["ROLE"] = "hub"
        app.extensions.update(meet_binding_service=binding, meet_dialog_service=service, meet_media_worker_key=hmac_key)

        def start(options):
            with app.app_context():
                result = service.start(
                    principal,
                    "synthetic",
                    {
                        "capabilities": [capability],
                        "duration_seconds": 120,
                        "chat_mode": "off",
                        **options,
                    },
                    parent="meet-test-parent",
                )
            cleanup.callback(cancel_fixture_dialog, app, service, principal, result["task_id"])
            return result

        yield SimpleNamespace(
            service=service,
            tasks=tasks,
            principal=principal,
            container=container,
            command=command,
            start=start,
            wav=tmp_path / "synthetic.wav",
        )
