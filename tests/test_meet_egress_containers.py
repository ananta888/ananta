"""Opt-in real private namespace proof; no production identity or public traffic."""

import json
import os
import time

import pytest

from tests.meet_egress_containers import EgressContainers, docker

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.skipif(os.environ.get("MEET_EGRESS_GATE") != "1", reason="explicit private egress-container gate"),
]


def test_invalid_installed_policy_never_allows_namespace_consumer_start(tmp_path):
    with EgressContainers(os.environ["MEET_EGRESS_IMAGE"]) as resources:
        config = tmp_path / "invalid.json"
        config.write_text('{"allow_all":true}')
        config.chmod(0o644)
        guard = resources.create("guard", configuration=config)
        until = time.monotonic() + 5
        while resources.inspect(guard)["State"]["Running"] and time.monotonic() < until:
            time.sleep(0.1)
        state = resources.inspect(guard)["State"]
        assert state["Running"] is False and state["ExitCode"] == 1
        assert state["Health"]["Status"] != "healthy"
        with pytest.raises(AssertionError):
            resources.create("worker", namespace=guard)


def test_installed_guard_denies_unadmitted_tcp_udp_dns_ipv6_and_hub_callers(tmp_path, record_property):
    with EgressContainers(os.environ["MEET_EGRESS_IMAGE"]) as resources:
        allowed, forbidden = resources.create("endpoint"), resources.create("endpoint")
        allowed_ip, forbidden_ip = resources.address(allowed), resources.address(forbidden)
        config = tmp_path / "egress.json"
        config.write_text(
            json.dumps(
                {
                    "schema": "ananta.meet-worker-egress.v1",
                    "endpoints": [
                        {"address": allowed_ip, "protocol": protocol, "port": port}
                        for protocol, port in (("tcp", 18080), ("udp", 18081))
                    ],
                    "names": [{"name": "allowed.example.test", "address": allowed_ip}],
                    "hub_clients": [allowed_ip],
                }
            )
        )
        config.chmod(0o644)
        guard = resources.create("guard", configuration=config)
        resources.healthy(guard)
        worker = resources.create("worker", namespace=guard)
        worker_row, guard_row = resources.inspect(worker), resources.inspect(guard)
        assert worker_row["Config"]["User"] == "1000:1000"
        assert worker_row["HostConfig"]["CapDrop"] == ["ALL"] and not worker_row["HostConfig"]["CapAdd"]
        assert {cap.removeprefix("CAP_") for cap in guard_row["HostConfig"]["CapAdd"]} == {
            "NET_ADMIN",
            "NET_BIND_SERVICE",
        }
        assert guard_row["HostConfig"]["Dns"] == ["127.0.0.1"]
        assert worker_row["HostConfig"]["NetworkMode"] == "container:" + guard
        assert all(not mount["RW"] for mount in guard_row["Mounts"] if mount["Type"] == "bind")
        result = resources.probe(worker, "client", allowed_ip, forbidden_ip)
        assert all(value is True for key, value in result.items() if not key.endswith("dns")), result
        assert result["fixed_dns"] == {"rcode": 0, "records": 1, "address": allowed_ip}
        assert result["tcp_dns"] == result["fixed_dns"]
        assert result["unknown_dns"]["rcode"] == 3 and result["unknown_dns"]["records"] == 0
        counts = resources.probe(forbidden, "http", "127.0.0.1", "18080", "/counts")
        assert counts == {"http": 0, "wrong_port": 0, "udp": 0, "dns": 0, "ipv6": 0}
        admitted_counts = resources.probe(allowed, "http", "127.0.0.1", "18080", "/counts")
        assert admitted_counts == {"http": 2, "wrong_port": 0, "udp": 1, "dns": 0, "ipv6": 0}
        guard_ip = resources.address(guard)
        assert resources.probe(allowed, "http", guard_ip, "8094", "/hit")["http"] == 1
        assert resources.probe(forbidden, "inbound", guard_ip) == {"blocked": True}
        assert resources.probe(worker, "http", "127.0.0.1", "8094", "/counts") == {
            "http": 1,
            "wrong_port": 0,
            "udp": 0,
            "dns": 0,
            "ipv6": 0,
        }
        # Inspect only this newly created namespace, never invoke a host firewall command.
        rules = docker("exec", guard, "/usr/sbin/ip6tables-save", "-t", "filter")
        assert ":OUTPUT DROP" in rules and "-j ACCEPT" not in rules
        record_property(
            "egress_guard",
            {
                "image": resources.image,
                "all_probes": result,
                "forbidden_packets": counts,
                "guard_healthy": True,
                "worker_nonroot_no_capabilities": True,
                "synthetic_policy": True,
                "production_release_evidence": False,
            },
        )
