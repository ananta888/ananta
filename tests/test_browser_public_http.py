"""No network: framing, memory budgets and remote-text-free HTTP errors."""

import io

import pytest

from ananta_contracts.browser_public_fetch import MAX_DOCUMENT_BYTES
from worker.meet_media.browser_public_http import PublicDocumentReader


def read(body=b"hello", headers=b"Content-Length: 5\r\n", *, status=b"HTTP/1.1 200 OK", clock=lambda: 0):
    raw = status + b"\r\nContent-Type: text/html; charset=utf-8\r\n" + headers + b"\r\n" + body
    return PublicDocumentReader(io.BytesIO(raw), deadline=1, clock=clock).read()


@pytest.mark.parametrize(
    "headers,body",
    [
        (b"Content-Length: 5\r\n", b"hello"),
        (b"", b"hello"),
        (b"Transfer-Encoding: chunked\r\n", b"2\r\nhe\r\n3\r\nllo\r\n0\r\n\r\n"),
        (b"Content-Length: 5\r\nSet-Cookie: private=discarded\r\n", b"hello"),
    ],
)
def test_supported_bounded_identity_framings(headers, body):
    assert read(body, headers) == b"hello"


@pytest.mark.parametrize(
    "headers",
    [
        b"Content-Length: 5\r\nContent-Length: 5\r\n",
        b"Content-Length: 5, 5\r\n",
        b"Content-Length: -1\r\n",
        b"Content-Length: 0\r\n",
        b"Content-Length: 524289\r\n",
        b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n",
        b"Transfer-Encoding: gzip\r\n",
        b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
        b"Content-Encoding: gzip\r\n",
        b"Content-Disposition: attachment\r\n",
        b"WWW-Authenticate: bearer secret\r\n",
        b"Proxy-Authenticate: secret\r\n",
        b"Upgrade: websocket\r\n",
        b"Trailer: cookie\r\n",
        b"Content-Type: image/png\r\n",
        b" folded: forbidden\r\n",
        b"Malformed\r\n",
        b"X-Bad: \x00secret\r\n",
        b"X-Large: " + b"x" * 4096 + b"\r\n",
        b"X-Many: value\r\n" * 65,
        (b"X: " + b"a" * 4000 + b"\r\n") * 5,
    ],
)
def test_ambiguous_or_unsupported_headers_fail_with_fixed_code(headers):
    with pytest.raises(ValueError, match="^browser_public_[a-z_]+$"):
        read(headers=headers)


@pytest.mark.parametrize(
    "status",
    [
        b"HTTP/1.1 302 redirect-secret",
        b"HTTP/1.1 401 private",
        b"HTTP/1.1 100 Continue",
        b"HTTP/2 200",
        b"HTTP/1.1 200 OK\nInjected",
    ],
)
def test_no_redirect_auth_interim_or_unsupported_status(status):
    with pytest.raises(ValueError, match="^browser_public_(?:status_denied|http_invalid)$"):
        read(status=status)


@pytest.mark.parametrize(
    "body",
    [
        b"1\r\na\r\n0\r\nSet-Cookie: secret\r\n\r\n",
        b"1;extension=yes\r\na\r\n0\r\n\r\n",
        b"-1\r\n",
        b"80001\r\n",
        b"1\r\naXY0\r\n\r\n",
        b"3\r\na",
        b"0\r\n\r\n",
        b"1\r\na\r\n" * 4096 + b"0\r\n\r\n",
    ],
)
def test_malformed_excessive_or_trailered_chunks_denied(body):
    with pytest.raises(ValueError):
        read(body, b"Transfer-Encoding: chunked\r\n")


def test_body_truncation_and_eof_oversize_denied():
    with pytest.raises(ValueError, match="incomplete"):
        read(b"x")
    with pytest.raises(ValueError, match="too_large"):
        read(b"x" * (MAX_DOCUMENT_BYTES + 1), b"")


def test_deadline_checked_after_blocking_read():
    clock_values = iter([0, 2])
    with pytest.raises(ValueError, match="expired"):
        read(clock=lambda: next(clock_values))


def test_header_budget_never_passes_negative_unbounded_readline():
    class Stream(io.BytesIO):
        def readline(self, size=-1):
            assert size > 0
            return super().readline(size)

    raw = b"HTTP/1.1 200 OK\r\n" + (b"X: " + b"a" * 4000 + b"\r\n") * 6
    with pytest.raises(ValueError):
        PublicDocumentReader(Stream(raw), deadline=1, clock=lambda: 0).read()
