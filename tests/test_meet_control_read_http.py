"""Actual loopback HTTP/signatures; no grant, browser or interactive approval."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.services.meet_dialog_controls import initial_controls
from ananta_contracts.meet_dialog import request_signature, response_signature
from tests.test_meet_dialog_transport import assignment
from worker.meet_media.contract import encode
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_control_exchange import DialogControlExchange


@contextmanager
def http_hub(tmp_path, monkeypatch, responses, *, speaker_floor=False, floor_projection=None, observe=None):
    key = b"synthetic-control-read-key-material"
    key_file = tmp_path / "control.key"
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    value, calls = assignment(), []
    if speaker_floor:
        value["speaker_floor"] = True
        value["capabilities"] = sorted(set(value["capabilities"]) | {"speech.publish", "chat.read", "chat.send"})

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            assert 0 < size <= 16384
            body = self.rfile.read(size)
            assert self.headers["X-Ananta-Dialog-Signature"] == request_signature(key, body)
            payload = json.loads(body)
            if observe is not None:
                observe(payload)
            calls.append(payload["action"])
            result = responses.pop(0)
            if isinstance(result, tuple):
                status, terminal = result
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("X-Ananta-Dialog-Terminal", terminal)
                self.end_headers()
                return
            if type(result) is int and result != 200:
                self.send_response(result)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raw = encode(
                {
                    "schema": "ananta.meet-dialog-state.v1",
                    "nonce": payload["nonce"],
                    "authorization": None,
                    "renewal": None,
                    "audio_job": None,
                    "controls": initial_controls(value["capabilities"], "off", "off", int(time.time()) * 1000),
                    **({"speaker_floor": floor_projection} if speaker_floor else {}),
                }
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "X-Ananta-Dialog-Signature",
                "invalid" if result == "bad-signature" else response_signature(key, body, raw),
            )
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key_file))
    monkeypatch.setenv("MEET_HUB_DIALOG_URL", f"http://127.0.0.1:{server.server_port}/api/meet/v1/internal/dialog")
    try:
        yield HubDialogClient(value), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def wait_for_result(exchange):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        result = exchange.poll()
        if result is not None:
            return result
        assert exchange.fresh_until is None, "a failed HTTP read cannot grant freshness"
        time.sleep(0.01)
    raise AssertionError("bounded signed control result missing")


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.parametrize("terminal", ["1", "", "unknown"])
def test_real_terminal_error_marker_never_acquires_gateway_retry(tmp_path, monkeypatch, status, terminal):
    with http_hub(tmp_path, monkeypatch, [(status, terminal), 200]) as (hub, calls):
        exchange = DialogControlExchange(hub, "ms_" + "a" * 32)
        try:
            with pytest.raises(ValueError, match="hub_revoked_or_unavailable"):
                wait_for_result(exchange)
            assert calls == ["exchange"] and exchange.fresh_until is None
        finally:
            exchange.close()


@pytest.mark.parametrize("status", [502, 503, 504])
def test_real_transient_http_status_recovers_only_after_new_signed_response(tmp_path, monkeypatch, status):
    with http_hub(tmp_path, monkeypatch, [status, 200]) as (hub, calls):
        exchange = DialogControlExchange(hub, "ms_" + "a" * 32)
        try:
            result = wait_for_result(exchange)
            assert result["schema"] == "ananta.meet-dialog-state.v1"
            assert calls == ["exchange", "exchange"]
            assert exchange.fresh_until > time.monotonic() and exchange.retry.attempts == 0
        finally:
            exchange.close()


@pytest.mark.parametrize("status", [401, 403, 409, 429, 500, "bad-signature"])
def test_real_policy_error_or_bad_signature_never_retries(tmp_path, monkeypatch, status):
    with http_hub(tmp_path, monkeypatch, [status, 200]) as (hub, calls):
        exchange = DialogControlExchange(hub, "ms_" + "a" * 32)
        try:
            with pytest.raises(ValueError, match="hub_revoked_or_unavailable") as error:
                wait_for_result(exchange)
            assert type(error.value) is ValueError and calls == ["exchange"]
            assert exchange.fresh_until is None and exchange.retry.attempts == 0
        finally:
            exchange.close()


def test_cleanup_callback_http_failure_is_not_classified_as_read_recovery(tmp_path, monkeypatch):
    with http_hub(tmp_path, monkeypatch, [503, 200]) as (hub, calls):
        with pytest.raises(ValueError, match="hub_revoked_or_unavailable") as error:
            hub.call("finish", status="failed")
        assert type(error.value) is ValueError and calls == ["finish"]
