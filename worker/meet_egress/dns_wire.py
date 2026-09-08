"""Bounded fixed-record DNS questions; no forwarding, resolution, logging or cache."""

import ipaddress
import re
import struct
from dataclasses import dataclass

from ananta_contracts.meet_egress import EgressPolicy

MAX_DNS_BYTES = 512


@dataclass(frozen=True, repr=False)
class Question:
    identity: int
    flags: int
    name: str
    kind: int
    dns_class: int
    wire: bytes


def question(packet):
    if type(packet) is not bytes or not 12 <= len(packet) <= MAX_DNS_BYTES:
        raise ValueError("meet_egress_dns_invalid")
    identity, flags, count, answers, authority, additional = struct.unpack("!6H", packet[:12])
    if flags & ~0x0130 or (count, answers, authority) != (1, 0, 0) or additional not in {0, 1}:
        raise ValueError("meet_egress_dns_invalid")
    cursor, labels = 12, []
    while cursor < len(packet):
        length = packet[cursor]
        cursor += 1
        if length == 0:
            break
        if not 1 <= length <= 63 or cursor + length > len(packet) or len(labels) >= 127:
            raise ValueError("meet_egress_dns_invalid")
        label = packet[cursor : cursor + length].decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label):
            raise ValueError("meet_egress_dns_invalid")
        labels.append(label.lower())
        cursor += length
    else:
        raise ValueError("meet_egress_dns_invalid")
    name = ".".join(labels)
    if not 1 <= len(name) <= 253 or cursor + 4 > len(packet):
        raise ValueError("meet_egress_dns_invalid")
    kind, dns_class = struct.unpack("!2H", packet[cursor : cursor + 4])
    cursor += 4
    wire = packet[12:cursor]
    if additional:
        # Only empty EDNS0 with a bounded advertised size; no options, query
        # compression, external client-subnet metadata or extended opcodes.
        if len(packet) - cursor != 11 or packet[cursor] != 0:
            raise ValueError("meet_egress_dns_invalid")
        opt, size, ttl, length = struct.unpack("!HHIH", packet[cursor + 1 :])
        if opt != 41 or not 512 <= size <= 4096 or ttl & ~0x8000 or length:
            raise ValueError("meet_egress_dns_invalid")
    elif cursor != len(packet):
        raise ValueError("meet_egress_dns_invalid")
    return Question(identity, flags, name, kind, dns_class, wire)


def answer(packet, policy):
    if type(policy) is not EgressPolicy:
        raise ValueError("meet_egress_dns_policy_invalid")
    try:
        query = question(packet)
    except (ValueError, UnicodeError):
        identity = struct.unpack("!H", packet[:2])[0] if type(packet) is bytes and len(packet) >= 2 else 0
        return struct.pack("!6H", identity, 0x8001, 0, 0, 0, 0)
    addresses = {row.name: row.address for row in policy.names}
    error = 5 if query.kind not in {1, 28} or query.dns_class != 1 else 3 if query.name not in addresses else 0
    record = b""
    if error == 0 and query.kind == 1:
        record = (
            bytes([192, 12]) + struct.pack("!HHIH", 1, 1, 0, 4) + ipaddress.IPv4Address(addresses[query.name]).packed
        )
    # No AD/DNSSEC or recursion-available claim; AAAA is explicitly empty.
    header = struct.pack("!6H", query.identity, 0x8400 | (query.flags & 0x0100) | error, 1, int(bool(record)), 0, 0)
    return header + query.wire + record
