"""Exact immutable operator ceilings; no live addresses or network privileges."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from ananta_contracts.meet_egress import MAX_BYTES, EgressPolicy, parse_egress_policy
from worker.meet_egress.rules import filter_rules


def configuration():
    return {
        "schema": "ananta.meet-worker-egress.v1",
        "endpoints": [
            {"address": "192.0.2.10", "protocol": "tcp", "port": 443},
            {"address": "192.0.2.20", "protocol": "udp", "port": 3478},
        ],
        "names": [{"name": "meet.example.test", "address": "192.0.2.10"}],
        "hub_clients": ["192.0.2.30"],
    }


def test_canonical_immutable_profile_has_order_independent_digest():
    value = configuration()
    policy = parse_egress_policy(json.dumps(value).encode())
    value["endpoints"].reverse()
    assert policy.digest == parse_egress_policy(json.dumps(value).encode()).digest
    with pytest.raises(FrozenInstanceError):
        policy.endpoints = ()
    assert policy.projection() == parse_egress_policy(json.dumps(policy.projection()).encode()).projection()
    value["endpoints"].clear()
    assert len(policy.endpoints) == 2


@pytest.mark.parametrize(
    "address",
    [
        "",
        None,
        123,
        "0.0.0.0",
        "0.1.2.3",
        "127.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "192.0.2.0/24",
        "192.000.2.1",
        "192.0.2.1\n-A OUTPUT -j ACCEPT",
        "meet.example.test",
    ],
)
def test_address_cannot_expand_to_names_metadata_ranges_or_loopback(address):
    value = configuration()
    value["endpoints"][0]["address"] = address
    with pytest.raises(ValueError, match="configuration_invalid"):
        parse_egress_policy(json.dumps(value).encode())


@pytest.mark.parametrize("port", [None, True, "443", 0, -1, 65536, 443.0, "1:65535", 53, 853])
def test_ports_are_exact_and_external_dns_is_not_admissible(port):
    value = configuration()
    value["endpoints"][0]["port"] = port
    with pytest.raises(ValueError):
        parse_egress_policy(json.dumps(value).encode())


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "*.example.test",
        "UPPER.test",
        "foo.",
        "-foo.test",
        "a..test",
        "localhost",
        "foo.local",
        "foo.localhost",
        "192.0.2.10",
        "ü.example",
        "a" * 64 + ".test",
    ],
)
def test_names_are_fixed_canonical_labels_not_wildcards_or_addresses(name):
    value = configuration()
    value["names"][0]["name"] = name
    with pytest.raises(ValueError):
        parse_egress_policy(json.dumps(value).encode())


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "duplicate-endpoint",
        "duplicate-name",
        "duplicate-client",
        "empty",
        "no-client",
        "unadmitted-name",
        "too-many-endpoints",
        "too-many-clients",
        "protocol",
    ],
)
def test_unknown_duplicate_unbound_or_excessive_scope_is_not_repaired(change):
    value = deepcopy(configuration())
    if change == "unknown":
        value["allow_all"] = True
    elif change.startswith("duplicate"):
        key = {"duplicate-endpoint": "endpoints", "duplicate-name": "names", "duplicate-client": "hub_clients"}[change]
        value[key].append(value[key][0])
    elif change in {"empty", "no-client"}:
        value["endpoints" if change == "empty" else "hub_clients"] = []
    elif change == "unadmitted-name":
        value["names"][0]["address"] = "192.0.2.40"
    elif change == "too-many-endpoints":
        value["endpoints"] = [{"address": "192.0.2.10", "port": 1000 + n, "protocol": "tcp"} for n in range(33)]
    elif change == "too-many-clients":
        value["hub_clients"] = ["192.0.2." + str(n) for n in range(1, 10)]
    else:
        value["endpoints"][0]["protocol"] = "all"
    with pytest.raises(ValueError):
        parse_egress_policy(json.dumps(value).encode())


@pytest.mark.parametrize(
    "raw",
    [b"", b"[]", b"null", b'{"a":1,"a":2}', b"NaN", b"[" * 2000, b"x" * (MAX_BYTES + 1), "not bytes", bytes([255])],
)
def test_hostile_json_is_bounded_without_private_error_contents(raw):
    with pytest.raises(ValueError, match="^meet_egress_configuration_invalid$"):
        parse_egress_policy(raw)


def test_filter_changes_only_private_filter_table_with_no_nat_or_implicit_egress():
    policy = parse_egress_policy(json.dumps(configuration()).encode())
    rules = filter_rules(policy)
    assert rules.startswith("*filter\n:INPUT DROP") and rules.endswith("COMMIT\n")
    assert "*nat" not in rules and "-P" not in rules and "0.0.0.0/0" not in rules
    assert "-A OUTPUT -d 192.0.2.10 -p tcp --dport 443 -m conntrack --ctstate NEW -j ACCEPT" in rules
    assert "-A INPUT -s 192.0.2.30 -p tcp --dport 8094" in rules
    assert all(
        "--dport" in line or "127.0.0.11" in line or "ESTABLISHED,RELATED" in line
        for line in rules.splitlines()
        if "-j ACCEPT" in line
    )
    assert (
        filter_rules(policy, ipv6=True)
        == "*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\nCOMMIT\n"
    )
    with pytest.raises(ValueError):
        filter_rules(policy, ipv6=1)
    with pytest.raises(ValueError):
        EgressPolicy((), (), ())
