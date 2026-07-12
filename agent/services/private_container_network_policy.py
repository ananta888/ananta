"""Shared DNS-pinning policy for internal container HTTP clients."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence

AddressResolver = Callable[[str, int], Sequence[str]]
_PRIVATE_CONTAINER_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class PrivateContainerResolutionError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def pin_private_container_address(
    hostname: str,
    port: int,
    *,
    resolver: AddressResolver | None = None,
) -> str:
    try:
        addresses = tuple(
            dict.fromkeys(
                str(value) for value in (resolver or _resolve_addresses)(hostname, port)
            )
        )
    except OSError as exc:
        raise PrivateContainerResolutionError(
            "worker_unavailable",
            "internal container name resolution failed",
        ) from exc
    if not addresses:
        raise PrivateContainerResolutionError(
            "worker_unavailable",
            "internal container resolved to no address",
        )
    parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise PrivateContainerResolutionError(
                "worker_address_forbidden",
                "internal container resolved to an invalid address",
            ) from exc
        if not _is_private_container_address(address):
            raise PrivateContainerResolutionError(
                "worker_address_forbidden",
                "internal service must resolve only to private container addresses",
            )
        parsed_addresses.append(address)
    return str(min(parsed_addresses, key=lambda item: (item.version, int(item))))


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        return (str(ipaddress.ip_address(hostname)),)
    except ValueError:
        return tuple(
            dict.fromkeys(
                str(item[4][0])
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            )
        )


def _is_private_container_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return False
    return any(
        address.version == network.version and address in network
        for network in _PRIVATE_CONTAINER_NETWORKS
    )
