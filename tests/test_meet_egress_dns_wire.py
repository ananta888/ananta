"""Fixed DNS records cannot resolve unknown names, options or alternate record types."""

import json
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ananta_contracts.meet_egress import parse_egress_policy
from tests.test_meet_egress_policy import configuration
from worker.meet_egress.dns_wire import answer, question


def query(name="meet.example.test", kind=1, *, flags=0x100, extra=b"", additional=0):
    labels = b"".join(bytes([len(label)]) + label.encode("ascii") for label in name.split(".")) + bytes([0])
    return struct.pack("!6H", 123, flags, 1, 0, 0, additional) + labels + struct.pack("!2H", kind, 1) + extra


def policy():
    return parse_egress_policy(json.dumps(configuration()).encode())


def test_answer_is_exact_pinned_ipv4_with_zero_ttl_no_dnssec_and_no_recursion():
    packet = query(flags=0x130)
    result = answer(packet, policy())
    identity, flags, questions, records, authority, additional = struct.unpack("!6H", result[:12])
    assert (identity, flags, questions, records, authority, additional) == (123, 0x8500, 1, 1, 0, 0)
    assert result[12:-16] == packet[12:]
    assert result[-16:] == bytes([192, 12]) + struct.pack("!HHIH", 1, 1, 0, 4) + bytes([192, 0, 2, 10])
    assert answer(query("MeEt.Example.Test"), policy())[-4:] == result[-4:]


@pytest.mark.parametrize("kind,code,records", [(1, 0, 1), (28, 0, 0), (16, 5, 0), (255, 5, 0), (65, 5, 0)])
def test_only_a_and_empty_aaaa_are_supported(kind, code, records):
    fields = struct.unpack("!6H", answer(query(kind=kind), policy())[:12])
    assert fields[1] & 15 == code and fields[3] == records


@pytest.mark.parametrize("name", ["private.meet.example.test", "foreign.test", "localhost", "192.0.2.10", "foo.local"])
def test_unknown_names_are_negative_without_resolver_fallback(name):
    fields = struct.unpack("!6H", answer(query(name), policy())[:12])
    assert fields[1] & 15 == 3 and fields[3] == 0


@pytest.mark.parametrize(
    "extra", [bytes([0]) + struct.pack("!HHIH", 41, 1232, 0, 0), bytes([0]) + struct.pack("!HHIH", 41, 4096, 0x8000, 0)]
)
def test_bounded_empty_edns0_does_not_change_answer_authority(extra):
    assert answer(query(extra=extra, additional=1), policy()) == answer(query(), policy())


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"a",
        b"x" * 513,
        query(extra=b"private"),
        query(flags=0x8100),
        query(flags=0x200),
        query(extra=bytes([0]) + struct.pack("!HHIH", 41, 4096, 1, 0), additional=1),
        struct.pack("!6H", 123, 0x100, 1, 0, 0, 0) + bytes([192, 12, 0, 1, 0, 1]),
        struct.pack("!6H", 123, 0x100, 2, 0, 0, 0) + query()[12:],
    ],
)
def test_malformed_compressed_duplicate_or_oversize_questions_have_bounded_formerr(packet):
    with pytest.raises(ValueError):
        question(packet)
    result = answer(packet, policy())
    assert len(result) == 12 and struct.unpack("!6H", result)[1:] == (0x8001, 0, 0, 0, 0)


@given(st.binary(max_size=1024))
def test_arbitrary_wire_never_escapes_bounded_response_or_reveals_config(packet):
    result = answer(packet, policy())
    assert type(result) is bytes and 12 <= len(result) <= 512
    assert b'"hub_clients":' not in result and b'"endpoints":' not in result
