"""Private Meet/issuer/bridge resources, independent of the chosen Hub host."""

import base64
import hashlib
import json
import os
import selectors
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace


@contextmanager
def private_receive(tmp_path, *, source):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

    from tests.meet_companion_build import require_current_browser_build
    from tests.meet_dialog_browser_fixture import docker
    from tests.test_meet_dialog_cross_repository import close_bridge

    if not isinstance(source, str) or source not in {"camera", "screen", "microphone", "screen-audio"}:
        raise ValueError("test_receive_source_invalid")
    gpu = source in {"microphone", "screen-audio"}
    bridge_kind = "audio" if gpu else "visual"
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

        commands = 0

        def command(name):
            nonlocal commands
            assert name in ({"source", "grant", "revoke", "speak"} if gpu else {"source", "grant", "revoke", "members"})
            assert commands < 240, "receive bridge command budget exhausted"
            commands += 1
            bridge.stdin.write(name + "\n")
            bridge.stdin.flush()
            return receive()

        ready = receive(60)
        certificate = x509.load_pem_x509_certificate(Path(ready["certificate"]).read_bytes())
        spki = base64.b64encode(
            hashlib.sha256(
                certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            ).digest()
        ).decode()
        network = json.loads(docker("network", "inspect", ready["test_network"]))[0]
        assert network["Internal"] is True and len(network["IPAM"]["Config"]) == 1
        yield SimpleNamespace(
            ready=ready,
            spki=spki,
            private=private,
            public=public,
            worker_key=worker_key,
            hmac_key=hmac_key,
            gpu=gpu,
            capability="audio.receive" if gpu else "video.receive",
            gateway=network["IPAM"]["Config"][0]["Gateway"],
            command=command,
            wav=tmp_path / "synthetic.wav",
        )
