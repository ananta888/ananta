"""Real synthetic HTTP bodies cannot keep a Hub read alive by trickling bytes."""

import io
import threading
import time
import urllib.error
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from agent.services import meet_media_transport as transport
from agent.services.meet_contract import MeetError
from tests.test_meet_media import result, turn
from worker.meet_media.contract import authenticate, encode, signature

pytestmark = pytest.mark.timeout(10)
KEY = b"synthetic-worker-read-key-32bytes!"


@contextmanager
def endpoint(monkeypatch, raw, *, interval=0, signed=True, announced_extra=0):
    stop = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(1)
            body = self.rfile.read(int(self.headers["Content-Length"]))
            authenticate(KEY, body, self.headers["X-Ananta-Task-Signature"])
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw) + announced_extra))
            self.send_header("X-Ananta-Result-Signature", signature(KEY, b"result-v1\0" + raw) if signed else "bad")
            self.end_headers()
            try:
                if not interval:
                    self.wfile.write(raw)
                else:
                    for index in range(len(raw)):
                        if stop.wait(interval):
                            break
                        self.wfile.write(raw[index : index + 1])
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(transport, "pin_private_container_address", lambda *_: "127.0.0.1")
    try:
        yield transport.HttpMediaWorker(f"http://worker.invalid:{server.server_port}/v1/turns", KEY), requests
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_trickling_signed_body_expires_under_original_turn_budget(monkeypatch):
    value = turn()
    # Only this transport's initial wall-clock conversion is controlled;
    # actual socket timing and the shared reader's monotonic clock stay real.
    monkeypatch.setattr(
        transport, "time", SimpleNamespace(time=lambda: value["deadline"] - 0.25, monotonic=time.monotonic)
    )
    with endpoint(monkeypatch, encode(result()), interval=0.003) as (worker, requests):
        started = time.monotonic()
        with pytest.raises(MeetError, match="meet_worker_unavailable"):
            worker.execute(value)
        assert time.monotonic() - started < 1.5
        assert requests == ["/v1/turns"]


@pytest.mark.parametrize("signed", [True, False])
def test_fast_real_response_keeps_exact_signature_contract(monkeypatch, signed):
    with endpoint(monkeypatch, encode(result()), signed=signed) as (worker, requests):
        if signed:
            assert worker.execute(turn()) == result()
        else:
            with pytest.raises(MeetError, match="meet_worker_result_unauthorized"):
                worker.execute(turn())
        assert requests == ["/v1/turns"]


def test_real_oversize_body_preserves_existing_error_code(monkeypatch):
    monkeypatch.setattr(transport, "MAX_RESULT_BYTES", 128)
    with endpoint(monkeypatch, b"x" * 129) as (worker, requests):
        with pytest.raises(MeetError, match="meet_worker_result_too_large") as error:
            worker.execute(turn())
        assert error.value.status == 502
        assert requests == ["/v1/turns"]


def test_premature_eof_is_rejected_even_when_partial_body_contains_a_signed_result(monkeypatch):
    with endpoint(monkeypatch, encode(result()), announced_extra=1) as (worker, requests):
        with pytest.raises(MeetError, match="meet_worker_unavailable"):
            worker.execute(turn())
        assert requests == ["/v1/turns"]


def test_expired_turn_never_resolves_or_sends_a_new_request(monkeypatch):
    pin = Mock(side_effect=AssertionError("expired turn must not resolve"))
    monkeypatch.setattr(transport, "pin_private_container_address", pin)
    worker = transport.HttpMediaWorker("http://worker.invalid:8094/v1/turns", KEY)
    with pytest.raises(MeetError, match="meet_worker_unavailable"):
        worker.execute(turn() | {"deadline": 0})
    pin.assert_not_called()


@pytest.mark.parametrize("failure", ["deadline", "read", "overflow", "signature", "encoding"])
def test_every_response_failure_closes_the_context_without_retry(monkeypatch, failure):
    response = MagicMock()
    response.__enter__.return_value = response
    raw = encode(result())
    response.headers = {"X-Ananta-Result-Signature": "bad"}
    if failure == "encoding":
        response.headers["Transfer-Encoding"] = "chunked"
        response.read1.side_effect = AssertionError("unsupported body must not be read")
    elif failure == "deadline":
        response.read1.side_effect = ValueError("persona_http_deadline_exceeded")
    elif failure == "read":
        response.read1.side_effect = OSError("private transport detail")
    elif failure == "overflow":
        monkeypatch.setattr(transport, "MAX_RESULT_BYTES", 2)
        response.read1.return_value = b"xxx"
    else:
        response.read1.side_effect = [raw, b""]
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *_: opener)
    monkeypatch.setattr(transport, "pin_private_container_address", lambda *_: "192.168.1.2")
    worker = transport.HttpMediaWorker("http://worker.invalid:8094/v1/turns", KEY)
    with pytest.raises(MeetError) as error:
        worker.execute(turn())
    assert "private" not in str(error.value)
    response.__exit__.assert_called_once()
    response.read.assert_not_called()
    if failure == "encoding":
        response.read1.assert_not_called()
    opener.open.assert_called_once()


def test_address_resolution_cannot_restart_the_original_turn_budget(monkeypatch):
    value = turn()
    ticks = iter([100.0, 102.0])
    monkeypatch.setattr(
        transport, "time", SimpleNamespace(time=lambda: value["deadline"] - 1, monotonic=lambda: next(ticks))
    )
    opener = Mock()
    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *_: opener)
    monkeypatch.setattr(transport, "pin_private_container_address", lambda *_: "192.168.1.2")
    worker = transport.HttpMediaWorker("http://worker.invalid:8094/v1/turns", KEY)
    with pytest.raises(MeetError, match="meet_worker_unavailable"):
        worker.execute(value)
    opener.open.assert_not_called()


def test_dialog_start_rejects_transfer_encoding_before_reading_or_retrying(monkeypatch):
    from tests.test_meet_dialog_transport import assignment

    response = MagicMock()
    response.__enter__.return_value = response
    response.headers = {"Transfer-Encoding": "chunked"}
    response.read1.side_effect = AssertionError("unsupported body must not be read")
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *_: opener)
    monkeypatch.setattr(transport, "pin_private_container_address", lambda *_: "192.168.1.2")
    worker = transport.HttpMediaWorker("http://worker.invalid:8094/v1/turns", KEY)
    with pytest.raises(MeetError, match="meet_dialog_worker_unavailable"):
        worker.start_dialog(assignment())
    response.read1.assert_not_called()
    response.__exit__.assert_called_once()
    opener.open.assert_called_once()


def test_capability_failure_rejects_transfer_encoding_without_reading_private_body():
    from agent.services.meet_media_failure_transport import worker_failure

    class UnreadBody(io.BytesIO):
        def read1(self, *_args):
            pytest.fail("unsupported body must not be read")

    body = UnreadBody(b"private upstream body")
    response = urllib.error.HTTPError(
        "http://worker.invalid/",
        503,
        "private upstream diagnostic",
        {"Transfer-Encoding": "chunked", "X-Ananta-Media-Failure-Signature": "nonempty"},
        body,
    )
    error = worker_failure(response, key=KEY, request_body=encode(turn()))
    assert error.code == "meet_worker_unavailable" and error.status == 503
    assert body.closed and "private" not in str(error)
