"""Closed operator-owned IPv4 transport ceiling, not execution authorization."""

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass

SCHEMA = "ananta.meet-worker-egress.v1"
MAX_BYTES = 16384


def address(value):
    if not isinstance(value, str):
        raise ValueError("meet_egress_address_invalid")
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise ValueError("meet_egress_address_invalid") from None
    if (
        str(parsed) != value
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed.is_reserved
        or value.startswith("0.")
    ):
        raise ValueError("meet_egress_address_invalid")
    return value


def hostname(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 253
        or value != value.lower()
        or value == "localhost"
        or value.endswith((".localhost", ".local"))
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split("."))
    ):
        raise ValueError("meet_egress_hostname_invalid")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("meet_egress_hostname_invalid")


@dataclass(frozen=True, order=True)
class EgressEndpoint:
    address: str
    protocol: str
    port: int

    def __post_init__(self):
        address(self.address)
        if (
            not isinstance(self.protocol, str)
            or self.protocol not in {"tcp", "udp"}
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
            or self.port in {53, 853}
        ):
            raise ValueError("meet_egress_endpoint_invalid")


@dataclass(frozen=True, order=True)
class EgressName:
    name: str
    address: str

    def __post_init__(self):
        hostname(self.name)
        address(self.address)


@dataclass(frozen=True)
class EgressPolicy:
    endpoints: tuple[EgressEndpoint, ...]
    names: tuple[EgressName, ...]
    hub_clients: tuple[str, ...]

    def __post_init__(self):
        if (
            type(self.endpoints) is not tuple
            or not 1 <= len(self.endpoints) <= 32
            or any(type(row) is not EgressEndpoint for row in self.endpoints)
            or type(self.names) is not tuple
            or len(self.names) > 32
            or any(type(row) is not EgressName for row in self.names)
            or type(self.hub_clients) is not tuple
            or not 1 <= len(self.hub_clients) <= 8
        ):
            raise ValueError("meet_egress_policy_invalid")
        for client in self.hub_clients:
            address(client)
        if (
            len(set(self.endpoints)) != len(self.endpoints)
            or len({row.name for row in self.names}) != len(self.names)
            or len(set(self.hub_clients)) != len(self.hub_clients)
            or not {row.address for row in self.names} <= {row.address for row in self.endpoints}
        ):
            raise ValueError("meet_egress_policy_invalid")

    def projection(self):
        return {
            "schema": SCHEMA,
            "endpoints": [
                {"address": row.address, "protocol": row.protocol, "port": row.port} for row in sorted(self.endpoints)
            ],
            "names": [{"name": row.name, "address": row.address} for row in sorted(self.names)],
            "hub_clients": sorted(self.hub_clients),
        }

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.projection(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_egress_policy(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("meet_egress_configuration_invalid")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("meet_egress_configuration_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
        if (
            type(value) is not dict
            or set(value) != {"schema", "endpoints", "names", "hub_clients"}
            or value["schema"] != SCHEMA
            or any(type(value[key]) is not list for key in ("endpoints", "names", "hub_clients"))
            or any(type(row) is not dict or set(row) != {"address", "protocol", "port"} for row in value["endpoints"])
            or any(type(row) is not dict or set(row) != {"name", "address"} for row in value["names"])
        ):
            raise ValueError()
        return EgressPolicy(
            tuple(EgressEndpoint(**row) for row in value["endpoints"]),
            tuple(EgressName(**row) for row in value["names"]),
            tuple(value["hub_clients"]),
        )
    except (ValueError, TypeError, RecursionError):
        raise ValueError("meet_egress_configuration_invalid") from None
