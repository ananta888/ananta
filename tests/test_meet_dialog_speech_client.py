"""Actual loopback HTTP speech handoff; synthetic media, no release evidence."""

import copy
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_dialog import response_signature
from ananta_contracts.meet_spoken_reply import MAX_SPOKEN_BYTES, spoken_request_signature, spoken_response_signature
from tests.test_meet_spoken_reply_contract import packet
from worker.meet_media.dialog_speech_client import HubSpeechClient

KEY = b"synthetic-local-http-speech-key-32"


@contextmanager
def endpoint(mode="ok", *, speaker_floor=False):
    request, binding, result = packet(22050)
    binding["deadline_ms"] = (int(time.time()) + 60) * 1000
    request["event"]["sent_at_ms"] = int(time.time() * 1000)
    result["reply"]["binding"] = copy.deepcopy(binding)
    if speaker_floor:
        result["reply"]["speaker_floor"] = {"id": "f" * 64, "sequence": 1, "expires_ms": binding["deadline_ms"] - 1000}
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, self.headers, raw))
            payload = json.loads(raw)
            assert self.headers["X-Ananta-Speech-Signature"] == spoken_request_signature(KEY, raw)
            value = copy.deepcopy(result) | {"nonce": payload["nonce"]}
            if mode == "scope":
                value["reply"]["binding"]["speech_revision"] += 1
            if mode == "nonce":
                value["nonce"] = "e" * 32
            if mode == "audio":
                value["reply"]["audio"]["base64"] = "bad"
            if mode == "empty":
                value.update(code="duplicate", reply=None)
            body = json.dumps(value).encode()
            if mode == "oversize":
                body = b" " * (MAX_SPOKEN_BYTES + 1)
            if mode == "delay":
                threading.Event().wait(0.3)
            self.send_response(307 if mode == "redirect" else 503 if mode == "status" else 200)
            if mode == "redirect":
                self.send_header("Location", "/forbidden-target")
            self.send_header("Content-Encoding", "gzip" if mode == "encoding" else "identity")
            if mode != "oversize":
                self.send_header("Content-Length", str(MAX_SPOKEN_BYTES + 1 if mode == "length" else len(body)))
            sign = response_signature if mode == "legacy_signature" else spoken_response_signature
            signature = sign(KEY, raw + (b" " if mode == "request_binding" else b""), body)
            self.send_header("X-Ananta-Speech-Signature", "bad" if mode == "signature" else signature)
            self.end_headers()
            try:
                self.wfile.write(body[:-4] if mode == "truncated" else body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/meet/v1/internal/dialog"
        yield (
            HubSpeechClient(url, KEY, request, time.monotonic() + 5, floor_required=speaker_floor),
            request["event"],
            binding,
            seen,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_real_http_receives_pcm_only_under_new_signature_and_closed_binding(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    with endpoint() as (client, event, binding, seen):
        result = client.reply(event, binding)
        assert result.pcm == b"\1\2" * 22050 and result.text == "Hallo"
        assert len(seen) == 1 and seen[0][0] == "/api/meet/v1/internal/dialog/speech"
        assert seen[0][1].get("Authorization") is None
        assert seen[0][1].get("X-Ananta-Dialog-Signature") is None
        assert "Hallo" not in repr(result)


def test_real_http_floor_handoff_is_authenticated_and_negotiated():
    with endpoint(speaker_floor=True) as (client, event, binding, seen):
        result = client.reply(event, binding)
        assert result.speaker_floor == {"id": "f" * 64, "sequence": 1, "expires_ms": binding["deadline_ms"] - 1000}
        assert len(result.pcm) == 44100 and len(seen) == 1


@pytest.mark.parametrize("mode", ["signature", "request_binding", "legacy_signature"])
def test_real_http_floor_does_not_weaken_response_authentication(mode):
    with endpoint(mode, speaker_floor=True) as (client, event, binding, seen):
        with pytest.raises(ValueError, match="revoked_or_unavailable"):
            client.reply(event, binding)
        assert len(seen) == 1


@pytest.mark.parametrize("server_floor", [True, False])
def test_real_http_floor_mode_mismatch_has_no_pcm_or_fallback(server_floor):
    with endpoint(speaker_floor=server_floor) as (client, event, binding, seen):
        client.floor_required = not server_floor
        with pytest.raises(ValueError, match="revoked_or_unavailable"):
            client.reply(event, binding)
        assert len(seen) == 1


@pytest.mark.parametrize(
    "mode",
    [
        "scope",
        "nonce",
        "audio",
        "oversize",
        "length",
        "signature",
        "legacy_signature",
        "request_binding",
        "redirect",
        "status",
        "encoding",
        "truncated",
    ],
)
def test_actual_http_rejects_bad_scopes_signatures_media_and_transport(mode):
    with endpoint(mode) as (client, event, binding, seen):
        with pytest.raises(ValueError, match="^meet_spoken_hub_revoked_or_unavailable$"):
            client.reply(event, binding)
        assert len(seen) == 1  # No redirect, retry or text/provider fallback.


def test_untrusted_body_is_never_parsed(monkeypatch):
    parser = Mock(side_effect=AssertionError("must authenticate first"))
    monkeypatch.setattr("worker.meet_media.dialog_speech_client.parse_spoken", parser)
    with endpoint("signature") as (client, event, binding, _seen):
        with pytest.raises(ValueError):
            client.reply(event, binding)
    parser.assert_not_called()


def test_duplicate_admission_has_no_pcm():
    with endpoint("empty") as (client, event, binding, _seen):
        assert client.reply(event, binding) is None


def test_http_deadline_and_preflight_expiry_are_bounded():
    with endpoint("delay") as (client, event, binding, seen):
        client.deadline = time.monotonic() + 0.08
        started = time.monotonic()
        with pytest.raises(ValueError):
            client.reply(event, binding)
        assert time.monotonic() - started < 1
        assert len(seen) == 1
        with pytest.raises(ValueError):
            client.reply(event, binding)
        assert len(seen) == 1


@pytest.mark.parametrize("patch", [{"extra": True}, {"text": "a" * 17000}])
def test_invalid_or_oversized_request_never_leaves_worker(patch):
    with endpoint() as (client, event, binding, seen):
        with pytest.raises(ValueError):
            client.reply(event | patch, binding)
        assert not seen


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.test/elsewhere",
        "https://user:pass@meet.test/api/meet/v1/internal/dialog",
        "https://meet.test/api/meet/v1/internal/dialog?url=other",
        "file:///api/meet/v1/internal/dialog",
    ],
)
def test_only_fixed_operator_dialog_endpoint_can_supply_speech(url):
    with pytest.raises(ValueError, match="meet_dialog_hub_endpoint_required"):
        HubSpeechClient(url, KEY, {}, time.monotonic() + 1)
