from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

import pytest
import yaml

from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import (
    EXPECTED_ENGINES,
    TURN_REPO_DIGEST,
    build_host_turn_command,
    locked_turn_udp_ports,
    validate_relay_report,
)

ROOT = Path(__file__).resolve().parents[2]


def _peer(identity: str) -> dict[str, object]:
    publisher = identity == "publisher"
    wrong_key = identity == "wrong-key-probe"
    return {
        "identity": identity,
        "peer_connections": 1,
        "local_video_publication_count": 1 if publisher else 0,
        "outbound_video_streams": 1 if publisher else 0,
        "inbound_video_streams": 0 if publisher else 1,
        "outbound_video_bytes": 2048 if publisher else 0,
        "inbound_video_bytes": 0 if publisher else 2048,
        "dropped_video_frames": 0,
        "selected_candidate_types": ["relay:host"],
        "decoded_samples": 0 if publisher or wrong_key else 3,
        "subscribed_publications": [],
    }


def _report() -> dict[str, object]:
    peers = [
        _peer("publisher"),
        _peer("receiver-1"),
        _peer("receiver-2"),
        _peer("receiver-3"),
        _peer("wrong-key-probe"),
    ]
    return {
        "schema": "ananta.semantic-sfu-three-peer-spike.v1",
        "release_evidence": False,
        "topology": {
            "publishers": 1,
            "receivers": 3,
            "expected_publisher_publications": 1,
        },
        "transport_profile": "turn_relay_required",
        "e2ee": {"enabled": True, "server_plaintext_access": False},
        "engines": [
            {
                "engine": engine,
                "peers": deepcopy(peers),
                "relay_required": True,
                "relay_selected": True,
                "verdict": "pass",
            }
            for engine in EXPECTED_ENGINES
        ],
        "verdict": "pass",
    }


def test_recomputes_successful_real_relay_flow_without_trusting_verdict() -> None:
    report = _report()
    report["verdict"] = "fail"

    assert validate_relay_report(report) == ()


def test_rejects_non_relay_candidate_even_when_report_claims_pass() -> None:
    report = _report()
    report["engines"][0]["peers"][1]["selected_candidate_types"] = [
        "relay:host",
        "host:host",
    ]

    reasons = validate_relay_report(report)

    assert "local_turn_relay_chromium_candidate_invalid" in reasons


def test_rejects_wrong_key_decode_and_missing_real_browser_engine() -> None:
    report = _report()
    report["engines"][0]["peers"][-1]["decoded_samples"] = 1
    report["engines"] = report["engines"][:1]

    reasons = validate_relay_report(report)

    assert "local_turn_relay_engine_inventory_invalid" in reasons
    assert "local_turn_relay_chromium_wrong_key_probe_invalid" in reasons


def test_accepts_multiple_simulcast_encodings_but_one_publication_and_peer_connection() -> None:
    report = _report()
    publisher = report["engines"][0]["peers"][0]
    publisher["outbound_video_streams"] = 3

    assert validate_relay_report(report) == ()

    publisher["peer_connections"] = 2
    assert "local_turn_relay_chromium_publisher_flow_invalid" in validate_relay_report(report)
    publisher["peer_connections"] = 1
    publisher["local_video_publication_count"] = 2
    assert "local_turn_relay_chromium_publisher_flow_invalid" in validate_relay_report(report)


def test_local_turn_compose_exposes_only_loopback_relay_ports() -> None:
    document = yaml.safe_load(
        (ROOT / "docker-compose.semantic-media.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["semantic-media-turn-gate"]

    # The container must discover and advertise its bridge address. Forcing
    # 127.0.0.1 here produces relay candidates that point back at each browser
    # process and makes real host-to-container TURN traffic unreachable.
    assert not any(str(argument).startswith("--external-ip=") for argument in service["command"])
    assert (
        "${ANANTA_SEMANTIC_MEDIA_TURN_GATE_BIND_IP:-127.0.0.1}:"
        "49160-49200:49160-49200/udp"
    ) in service["ports"]
    assert all(
        str(port).startswith(
            "${ANANTA_SEMANTIC_MEDIA_TURN_GATE_BIND_IP:-127.0.0.1}:"
        )
        for port in service["ports"]
    )


def test_host_turn_command_is_isolated_and_namespaced() -> None:
    command = build_host_turn_command(
        container_name="ananta-sfu-relay-012345abcdef-turn-host",
        host="192.0.2.10",
        listening_port=44_347,
        relay_min_port=50_000,
        relay_max_port=50_063,
        username="local-user",
        password="x" * 48,
    )

    assert command[:3] == ["docker", "run", "--detach"]
    assert "--network" in command
    assert command[command.index("--network") + 1] == "host"
    assert "--rm" in command
    assert "--read-only" in command
    assert TURN_REPO_DIGEST in command
    assert "--listening-ip=192.0.2.10" in command
    assert "--relay-ip=192.0.2.10" in command
    assert "--listening-port=44347" in command
    assert "--min-port=50000" in command
    assert "--max-port=50063" in command
    assert "--publish" not in command
    assert "-p" not in command


def test_turn_port_reservation_serializes_parallel_runners() -> None:
    acquired = Event()

    def reserve_second() -> None:
        with locked_turn_udp_ports("127.0.0.1", range_size=32):
            acquired.set()

    with locked_turn_udp_ports("127.0.0.1", range_size=32):
        contender = Thread(target=reserve_second, daemon=True)
        contender.start()
        assert acquired.wait(0.2) is False
    assert acquired.wait(2)
    contender.join(timeout=2)
    assert contender.is_alive() is False


def test_local_spike_defaults_are_non_release_tmp_diagnostics() -> None:
    spike = (ROOT / "scripts/spikes/semantic_sfu_three_peer.mjs").read_text(
        encoding="utf-8"
    )
    group_runner = (ROOT / "scripts/e2e/semantic_sfu_group_e2e.py").read_text(
        encoding="utf-8"
    )

    assert "/tmp/ananta-semantic-sfu-three-peer.json" in spike
    assert "release_evidence: false" in spike
    assert "artifacts/domain" not in spike
    assert "artifacts/domain" not in group_runner


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/e2e/sfu_broadcast_harness.py",
        "scripts/e2e/sfu_broadcast_browser_e2e.py",
    ],
)
def test_sfu_broadcast_cli_supports_repo_root_direct_invocation(
    relative_path: str,
) -> None:
    result = subprocess.run(
        [sys.executable, relative_path, "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
