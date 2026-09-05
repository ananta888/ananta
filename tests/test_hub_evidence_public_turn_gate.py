from __future__ import annotations

from scripts.run_hub_evidence_public_turn_gate import (
    project_probe_report,
    projection_passed,
)


def _report() -> dict:
    rows = []
    for engine in ("chromium", "firefox"):
        for transport in ("udp", "tcp", "tls"):
            rows.append(
                {
                    "engine": engine,
                    "transport": transport,
                    "connected": True,
                    "senderIceState": "connected",
                    "receiverIceState": "connected",
                    "localCandidateType": "relay",
                    "remoteCandidateType": "relay",
                    "protocol": "udp",
                    "relayProtocol": transport if transport != "tls" else "tcp",
                    "pairState": "succeeded",
                    "bytesSent": 100,
                    "bytesReceived": 90,
                    "applicationBytesSent": 80,
                    "applicationBytesReceived": 80,
                    "applicationBytesEchoed": 80,
                    "address": "must-not-be-projected",
                }
            )
    return {
        "schema": "ananta.public-turn-relay-probe.v1",
        "status": "passed",
        "reason_code": "public_turn_relay_probe_passed",
        "public_host": "webrtc.ananta.de",
        "credential_ttl_seconds": 600,
        "engines": ["chromium", "firefox"],
        "transports": ["udp", "tcp", "tls"],
        "results": rows,
        "human_intervention_required": False,
        "production_capacity": False,
    }


def test_projection_keeps_bounded_relay_metrics_and_passes_matrix() -> None:
    projection = project_probe_report(_report())

    assert projection_passed(projection, host="webrtc.ananta.de")
    assert all("address" not in row for row in projection["results"])


def test_projection_rejects_direct_candidate_or_missing_transport() -> None:
    projection = project_probe_report(_report())
    projection["results"][0]["localCandidateType"] = "host"
    projection["results"].pop()

    assert not projection_passed(projection, host="webrtc.ananta.de")
