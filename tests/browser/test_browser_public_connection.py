"""Resolver and socket seams demonstrate no second lookup or proxy fallback."""

import socket
import ssl
from unittest.mock import Mock

import pytest

from ananta_contracts.browser_public_fetch import PublicFetchTarget
from worker.meet_media.browser_public_connection import public_address, public_connection


def row(address="8.8.8.8", port=443):
    v6 = ":" in address
    return (
        socket.AF_INET6 if v6 else socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        (address, port, 0, 0) if v6 else (address, port),
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row()] * 17,
        [row(), row("127.0.0.1")],
        [row("10.0.0.1")],
        [row("169.254.169.254")],
        [row("100.64.0.1")],
        [row("224.0.0.1")],
        [row("::1")],
        [row("fc00::1")],
        [row("::ffff:127.0.0.1")],
        [row("64:ff9b::808:808")],
        [row("2002:808:808::")],
        [row("fe80::1%eth0")],
        [row(port=80)],
        [(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))],
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_UDP, "", ("8.8.8.8", 443))],
        [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700::1111", 443, 0, 1))],
    ],
)
def test_mixed_private_unsupported_or_scoped_dns_never_connects(rows):
    sockets = Mock()
    with pytest.raises(ValueError):
        with public_connection(
            PublicFetchTarget.parse("https://example.com"), resolver=Mock(return_value=rows), socket_factory=sockets
        ):
            pytest.fail("must not yield")
    sockets.assert_not_called()


def test_single_resolution_numeric_connection_and_original_tls_hostname():
    resolver = Mock(return_value=[row(), row("2606:4700:4700::1111")])
    sockets, tls = Mock(), Mock()
    tls.return_value.check_hostname = True
    tls.return_value.verify_mode = ssl.CERT_REQUIRED
    target = PublicFetchTarget.parse("https://example.com/docs")
    with public_connection(target, resolver=resolver, socket_factory=sockets, tls_factory=tls) as stream:
        assert stream is tls.return_value.wrap_socket.return_value
    resolver.assert_called_once_with("example.com", 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    sockets.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    sockets.return_value.connect.assert_called_once_with(("8.8.8.8", 443))
    tls.return_value.wrap_socket.assert_called_once_with(sockets.return_value, server_hostname="example.com")
    stream.close.assert_called_once()


def test_ipv6_numeric_selection():
    target = PublicFetchTarget.parse("https://example.com")
    assert public_address(target, resolver=Mock(return_value=[row("2606:4700:4700::1111")])) == (
        socket.AF_INET6,
        "2606:4700:4700::1111",
    )


@pytest.mark.parametrize("fault", ["connect", "tls", "unverified"])
def test_transport_failure_always_closes_numeric_socket(fault):
    sockets, tls = Mock(), Mock()
    tls.return_value.check_hostname = True
    tls.return_value.verify_mode = ssl.CERT_REQUIRED
    if fault == "connect":
        sockets.return_value.connect.side_effect = OSError("private diagnostic")
    elif fault == "tls":
        tls.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("secret")
    else:
        tls.return_value.check_hostname = False
    with pytest.raises((ValueError, OSError)):
        with public_connection(
            PublicFetchTarget.parse("https://example.com"),
            resolver=Mock(return_value=[row()]),
            socket_factory=sockets,
            tls_factory=tls,
        ):
            pytest.fail("must fail closed")
    sockets.return_value.close.assert_called_once()
