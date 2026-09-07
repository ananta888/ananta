"""Real signed callback HTTP proves explicit old/new image negotiation."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.services.meet_dialog_controls import initial_controls
from ananta_contracts.meet_dialog import request_signature, response_signature
from ananta_contracts.meet_dialog_voice import content_digest
from tests.test_meet_dialog_transport import assignment
from worker.meet_media.contract import encode
from worker.meet_media.dialog_client import HubDialogClient


@pytest.mark.parametrize(
    "negotiated,projection,valid",
    [
        (False, None, True),
        (True, "valid", True),
        (True, None, False),
        (False, "valid", False),
        (True, "invalid", False),
    ],
)
@pytest.mark.parametrize(
    "source,flag,capability",
    [("avatar", "avatar_images", "avatar.publish"), ("voice", "voice_profiles", "speech.publish")],
)
def test_signed_callback_never_silently_upgrades_or_downgrades_avatar_protocol(
    tmp_path, monkeypatch, negotiated, projection, valid, source, flag, capability
):
    key = b"synthetic-avatar-callback-key-only"
    key_file = tmp_path / "callback.key"
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    value = assignment() | {"capabilities": [capability]}
    if negotiated:
        value[flag] = True
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(self.path)
            assert self.headers["X-Ananta-Dialog-Signature"] == request_signature(key, body)
            payload = json.loads(body)
            response = {
                "schema": "ananta.meet-dialog-state.v1",
                "nonce": payload["nonce"],
                "authorization": None,
                "renewal": None,
                "audio_job": None,
                "controls": initial_controls(value["capabilities"], "off", "off", int(time.time()) * 1000),
            }
            if projection is not None:
                response[source] = (
                    {
                        "mode": "neutral-ai-v1",
                        "state": "paused",
                        "binding": None,
                        "reference": None,
                    }
                    if source == "avatar"
                    else {
                        "mode": "configured-piper-v1",
                        "state": "paused",
                        "speech_revision": 1,
                        "selection_digest": content_digest({"mode": "configured-piper-v1"}),
                        "profile": None,
                    }
                ) | ({"unexpected": True} if projection == "invalid" else {})
            raw = encode(response)
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Ananta-Dialog-Signature", response_signature(key, body, raw))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key_file))
    monkeypatch.setenv("MEET_HUB_DIALOG_URL", f"http://127.0.0.1:{server.server_port}/api/meet/v1/internal/dialog")
    try:
        client = HubDialogClient(value)
        if valid:
            result = client.call("exchange", meet_session_id="ms_" + "a" * 32)
            assert (source in result) == negotiated
        else:
            with pytest.raises(ValueError, match="hub_revoked_or_unavailable"):
                client.call("exchange", meet_session_id="ms_" + "a" * 32)
        assert seen == ["/api/meet/v1/internal/dialog"]
        if not negotiated and source == "avatar":
            with pytest.raises(ValueError, match="not_negotiated"):
                client.avatar_image({}, {})
            assert len(seen) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
