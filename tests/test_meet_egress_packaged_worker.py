"""Actual packaged Worker liveness and transport ceiling, without GPU or Task."""

import json
import os

import pytest

from tests.meet_egress_containers import EgressContainers
from tests.meet_egress_packaged_worker import PackagedEgressWorker

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.skipif(os.environ.get("MEET_EGRESS_GATE") != "1", reason="explicit private egress-container gate"),
]


def test_packaged_media_worker_retains_health_and_authentication_under_guard(tmp_path, record_property):
    with EgressContainers(os.environ["MEET_EGRESS_IMAGE"]) as resources:
        admitted, forbidden = resources.create("endpoint"), resources.create("endpoint")
        admitted_ip, forbidden_ip = resources.address(admitted), resources.address(forbidden)
        config = tmp_path / "egress.json"
        config.write_text(
            json.dumps(
                {
                    "schema": "ananta.meet-worker-egress.v1",
                    "endpoints": [
                        {"address": admitted_ip, "protocol": protocol, "port": port}
                        for protocol, port in (("tcp", 18080), ("udp", 18081))
                    ],
                    "names": [{"name": "allowed.example.test", "address": admitted_ip}],
                    "hub_clients": [admitted_ip],
                }
            )
        )
        config.chmod(0o644)
        guard = resources.create("guard", configuration=config)
        resources.healthy(guard)
        key = tmp_path / "worker-key"
        key.write_bytes(b"synthetic-egress-worker-key-only-no-authorized-tasks")
        key.chmod(0o400)
        worker = PackagedEgressWorker(resources, guard, os.environ["MEET_EGRESS_WORKER_IMAGE"])
        worker.start(key)
        worker.no_executed_leases()
        result = worker.probe("client", admitted_ip, forbidden_ip)
        assert all(value is True for name, value in result.items() if not name.endswith("dns")), result
        assert result["fixed_dns"] == result["tcp_dns"] == {"rcode": 0, "records": 1, "address": admitted_ip}
        assert result["unknown_dns"]["rcode"] == 3
        assert resources.probe(forbidden, "http", "127.0.0.1", "18080", "/counts") == {
            "http": 0,
            "wrong_port": 0,
            "udp": 0,
            "dns": 0,
            "ipv6": 0,
        }
        guard_ip = resources.address(guard)
        # The allowed network caller still cannot obtain the loopback-only
        # application health response. The guard never supplies an API bypass.
        assert resources.probe(admitted, "status", guard_ip) == {"status": 404}
        assert resources.probe(admitted, "unsigned-turn", guard_ip) == {
            "status": 409,
            "body": {"error": {"code": "meet_turn_unauthorized"}},
        }
        assert resources.probe(forbidden, "blocked-status", guard_ip) == {"blocked": True}
        assert worker.probe("http", "127.0.0.1", "8094", "/healthz") == {
            "schema": "ananta.meet-worker-health.v1",
            "state": "alive",
        }
        worker.no_executed_leases()
        record_property(
            "packaged_worker_egress",
            {
                "worker_image": worker.image,
                "guard_image": resources.image,
                "healthy_nonroot_worker": True,
                "executed_leases": 0,
                "all_probes": result,
                "synthetic_policy": True,
                "gpu_inference": False,
                "turn_session": False,
                "production_release_evidence": False,
            },
        )
