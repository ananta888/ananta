"""Actual bounded loopback HTTP; synthetic image bytes, no live release claim."""

import copy
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_avatar_image import MAX_AVATAR_IMAGE_BYTES, image_request_signature, image_response_signature
from ananta_contracts.meet_dialog import response_signature
from tests.test_meet_avatar_image_contract import packet
from worker.meet_media.dialog_avatar_image_client import HubAvatarImageClient

KEY = b"synthetic-avatar-image-test-key-32"


@contextmanager
def endpoint(mode="ok"):
    original, response = packet()
    binding = original["binding"] | {"deadline_ms": (int(time.time()) + 60) * 1000}
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, self.headers, raw))
            assert self.headers["X-Ananta-Avatar-Signature"] == image_request_signature(KEY, raw)
            request = json.loads(raw)
            value = copy.deepcopy(response) | {"nonce": request["nonce"], "binding": dict(binding)}
            if mode == "scope":
                value["binding"]["avatar_revision"] += 1
            if mode == "nonce":
                value["nonce"] = "d" * 32
            if mode == "image":
                value["image"]["png"] = "bad"
            body = json.dumps(value).encode()
            if mode == "oversize":
                body = b" " * (MAX_AVATAR_IMAGE_BYTES + 1)
            if mode == "delay":
                threading.Event().wait(0.3)
            self.send_response(307 if mode == "redirect" else 503 if mode == "status" else 200)
            if mode == "redirect":
                self.send_header("Location", "/forbidden")
            self.send_header("Content-Encoding", "gzip" if mode == "encoding" else "identity")
            if mode != "oversize":
                self.send_header("Content-Length", str(MAX_AVATAR_IMAGE_BYTES + 1 if mode == "length" else len(body)))
            signer = response_signature if mode == "legacy_signature" else image_response_signature
            signed = signer(KEY, raw + (b" " if mode == "request_binding" else b""), body)
            self.send_header("X-Ananta-Avatar-Signature", "bad" if mode == "signature" else signed)
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
            HubAvatarImageClient(url, KEY, binding, time.monotonic() + 5),
            binding,
            response["image"]["reference"],
            seen,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_real_http_image_response_is_request_bound_and_ignores_environment_proxy(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    with endpoint() as (client, binding, reference, seen):
        result = client.fetch(binding, reference)
        assert result["reference"] == reference and result["png"]
        assert len(seen) == 1 and seen[0][0] == "/api/meet/v1/internal/dialog/avatar-image"
        assert seen[0][1].get("Authorization") is None and seen[0][1].get("X-Ananta-Dialog-Signature") is None


@pytest.mark.parametrize(
    "mode",
    [
        "scope",
        "nonce",
        "image",
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
def test_bad_image_authority_signatures_or_transport_fail_without_retry_or_redirect(mode):
    with endpoint(mode) as (client, binding, reference, seen):
        with pytest.raises(ValueError, match="^meet_avatar_image_hub_revoked_or_unavailable$"):
            client.fetch(binding, reference)
        assert len(seen) == 1


def test_unauthenticated_image_bytes_are_not_parsed(monkeypatch):
    parser = Mock(side_effect=AssertionError("authenticate before parse"))
    monkeypatch.setattr("worker.meet_media.dialog_avatar_image_client.parse_image_message", parser)
    with endpoint("signature") as (client, binding, reference, seen):
        with pytest.raises(ValueError):
            client.fetch(binding, reference)
    parser.assert_not_called()


def test_network_deadline_and_assignment_expiry_are_bounded_without_retry():
    with endpoint("delay") as (client, binding, reference, seen):
        client.deadline = time.monotonic() + 0.08
        started = time.monotonic()
        with pytest.raises(ValueError):
            client.fetch(binding, reference)
        assert time.monotonic() - started < 1 and len(seen) == 1
        with pytest.raises(ValueError):
            client.fetch(binding, reference)
        assert len(seen) == 1


@pytest.mark.parametrize("change", ["binding", "assignment", "reference", "expired"])
def test_invalid_or_foreign_hydration_never_leaves_worker(change):
    with endpoint() as (client, binding, reference, seen):
        if change == "binding":
            binding["avatar_revision"] = True
        elif change == "assignment":
            binding["task_id"] = "foreign"
        elif change == "reference":
            reference["tenant_id"] = "foreign"
        else:
            binding["deadline_ms"] = 1
        with pytest.raises(ValueError):
            client.fetch(binding, reference)
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
def test_hydration_only_uses_fixed_operator_hub_endpoint(url):
    with pytest.raises(ValueError, match="hub_endpoint_required"):
        HubAvatarImageClient(url, KEY, {}, time.monotonic() + 1)
