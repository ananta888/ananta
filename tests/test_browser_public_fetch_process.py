"""Redacted fetch process, exact HTTP request and actual bounded child cancellation."""

import io
import json
import sys
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from ananta_contracts.browser_public_fetch import MAX_FETCH_WIRE_BYTES, decode_document, document_result
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner, ProcessResult
from worker.meet_media.browser_public_fetch_child import fetch_document
from worker.meet_media.browser_public_fetch_process import PublicDocumentFetch

REQUEST = {
    "schema": "ananta.browser-public-fetch.v1",
    "url": "https://example.com/docs",
    "allowed_origins": ["https://example.com"],
}


def test_child_only_sends_fixed_get_and_discards_remote_headers():
    stream = Mock()
    stream.makefile.return_value = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 5\r\nSet-Cookie: secret\r\n\r\nhello"
    )

    @contextmanager
    def connect(target):
        assert target.origin == "https://example.com"
        yield stream

    assert decode_document(fetch_document(REQUEST, connect=connect)) == "hello"
    sent = stream.sendall.call_args.args[0]
    assert sent.startswith(b"GET /docs HTTP/1.1\r\nHost: example.com\r\n")
    assert b"Cookie" not in sent and b"Authorization" not in sent and b"Accept-Encoding: identity" in sent


def test_parent_uses_bounded_existing_port_and_rechecks_current():
    current, runner = Mock(), Mock()
    runner.run.return_value = ProcessResult(0, json.dumps(document_result(b"hello")).encode())
    assert (
        PublicDocumentFetch(require_current=current, runner=runner).fetch(REQUEST, deadline=time.monotonic() + 10)
        == "hello"
    )
    args, kwargs = runner.run.call_args
    assert args[0] == [sys.executable, "-m", "worker.meet_media.browser_public_fetch_child"]
    assert "example.com" not in str(args)
    assert json.loads(kwargs["input_payload"]) == REQUEST
    assert kwargs["max_stdout_bytes"] == MAX_FETCH_WIRE_BYTES
    assert 0 < kwargs["timeout_seconds"] <= 3
    assert kwargs["cancellation_check"] is current
    assert current.call_count == 2


@pytest.mark.parametrize(
    "result",
    [
        ProcessResult(1, b"secret remote error"),
        ProcessResult(0, b"not json"),
        ProcessResult(0, b'{"error":"secret"}'),
        ProcessResult(0, b'{"schema":"x","schema":"y"}'),
    ],
)
def test_failed_or_malformed_child_output_is_redacted(result):
    runner = Mock()
    runner.run.return_value = result
    with pytest.raises(ValueError, match="^browser_public_fetch_failed$"):
        PublicDocumentFetch(require_current=lambda: None, runner=runner).fetch(REQUEST, deadline=time.monotonic() + 5)


def test_revocation_after_result_prevents_consumption():
    current, runner = Mock(side_effect=[None, ValueError("revoked secret")]), Mock()
    runner.run.return_value = ProcessResult(0, json.dumps(document_result(b"hello")).encode())
    with pytest.raises(ValueError, match="^browser_public_fetch_failed$"):
        PublicDocumentFetch(require_current=current, runner=runner).fetch(REQUEST, deadline=time.monotonic() + 5)


class SlowChildRunner:
    """Real supervisor with an intentionally stalled child; never a network fixture."""

    def run(self, argv, **kwargs):
        script = (
            "import socket,time; socket.getaddrinfo=lambda *a,**k: time.sleep(30); "
            "from worker.meet_media.browser_public_fetch_child import main; raise SystemExit(main())"
        )
        return BoundedSubprocessRunner().run([argv[0], "-c", script], **kwargs)


@pytest.mark.parametrize("cancel", [False, True])
def test_actual_stalled_dns_child_is_killed_on_deadline_or_revocation(cancel):
    started = time.monotonic()

    def current():
        if cancel and time.monotonic() - started > 0.15:
            raise ValueError("revoked")

    with pytest.raises(ValueError, match="^browser_public_fetch_failed$"):
        PublicDocumentFetch(require_current=current, runner=SlowChildRunner()).fetch(
            REQUEST, deadline=started + (3 if cancel else 0.35)
        )
    assert time.monotonic() - started < 2


def test_actual_child_rejects_private_target_without_network():
    runner = BoundedSubprocessRunner()
    from pathlib import Path

    result = runner.run(
        [sys.executable, "-m", "worker.meet_media.browser_public_fetch_child"],
        input_payload=json.dumps({**REQUEST, "url": "http://127.0.0.1"}).encode(),
        max_stdout_bytes=4096,
        timeout_seconds=3,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "browser_public_fetch_failed"}
