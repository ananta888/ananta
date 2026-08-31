"""Resolve the host-side private address reachable from isolated containers."""

from __future__ import annotations

import ipaddress
import socket


def private_runtime_host() -> str:
    networks = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    candidates = {str(item[4][0]) for item in socket.getaddrinfo(socket.gethostname(), 0, type=socket.SOCK_STREAM)}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_probe:
            route_probe.connect(("198.18.0.1", 9))
            candidates.add(str(route_probe.getsockname()[0]))
    except OSError:
        pass
    for candidate in sorted(candidates):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if any(address in network for network in networks):
            return candidate
    raise RuntimeError("semantic_media_private_runtime_address_unavailable")
