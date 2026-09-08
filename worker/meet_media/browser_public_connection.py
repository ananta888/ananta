"""Resolve once, reject mixed/private DNS, connect only to a validated numeric IP."""

import ipaddress
import socket
import ssl
from contextlib import contextmanager

from ananta_contracts.browser_navigation_target import restricted_address


def public_address(target, *, resolver=socket.getaddrinfo):
    rows = resolver(target.hostname, target.port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not rows or len(rows) > 16:
        raise ValueError("browser_public_dns_denied")
    addresses = set()
    for family, kind, protocol, _canonical, sockaddr in rows:
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or kind != socket.SOCK_STREAM
            or protocol != socket.IPPROTO_TCP
        ):
            raise ValueError("browser_public_dns_denied")
        expected = 2 if family == socket.AF_INET else 4
        if len(sockaddr) != expected or sockaddr[1] != target.port or "%" in sockaddr[0]:
            raise ValueError("browser_public_dns_denied")
        address = ipaddress.ip_address(sockaddr[0])
        if address.version != (4 if family == socket.AF_INET else 6) or restricted_address(address):
            raise ValueError("browser_public_dns_denied")
        if family == socket.AF_INET6 and sockaddr[2:] != (0, 0):
            raise ValueError("browser_public_dns_denied")
        addresses.add((family, str(address)))
    return sorted(addresses)[0]


@contextmanager
def public_connection(
    target, *, resolver=socket.getaddrinfo, socket_factory=socket.socket, tls_factory=ssl.create_default_context
):
    family, address = public_address(target, resolver=resolver)
    stream = socket_factory(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        stream.settimeout(2)
        stream.connect((address, target.port) if family == socket.AF_INET else (address, target.port, 0, 0))
        if target.scheme == "https":
            context = tls_factory()
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise ValueError("browser_public_tls_verification_required")
            stream = context.wrap_socket(stream, server_hostname=target.hostname)
        yield stream
    finally:
        stream.close()
