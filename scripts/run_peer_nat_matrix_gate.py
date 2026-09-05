#!/usr/bin/env python3
"""Run a headless real-browser ICE/TURN matrix under Hub test evidence."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ananta_contracts.peer_overlay import (  # noqa: E402
    OverlayCapability,
    OverlayEpochs,
    OverlayTrafficClass,
    PeerRouteLease,
)
from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import (  # noqa: E402
    TURN_REPO_DIGEST,
    build_host_turn_command,
    local_non_loopback_ipv4,
    locked_turn_udp_ports,
    remove_owned_turn_container,
    reserve_port,
    run_command,
    verify_container_image,
    wait_for_running_container,
)
from scripts.hub_browser_test_evidence import (  # noqa: E402
    HubBrowserTestRun,
    host_environment,
    localhost_origin,
)

FRONTEND = ROOT / "frontend-angular"
TASK_ID = "DPM-NET-003"
EXPECTED_ENGINES = frozenset({"chromium", "firefox"})
SOURCE_PATHS = (
    Path("ananta_contracts/peer_overlay.py"),
    Path("frontend-angular/playwright.peer-nat-matrix.config.ts"),
    Path("frontend-angular/tests/peer-nat-matrix.spec.ts"),
    Path("scripts/e2e/sfu_broadcast_local_turn_relay_e2e.py"),
    Path("scripts/hub_browser_test_evidence.py"),
    Path("scripts/run_peer_nat_matrix_gate.py"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-db", type=Path, default=ROOT / "data/peer-nat-evidence.sqlite3")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "artifacts/test-gates/peer-nat-matrix.json"
    )
    args = parser.parse_args()
    environment = host_environment(frontend=FRONTEND)
    profile = {
        "schema": "ananta.peer-nat-browser-profile.v1",
        "engines": sorted(EXPECTED_ENGINES),
        "scenarios": ["direct", "turn_udp", "turn_tcp", "network_switch", "blocked_turn"],
        "blocked_timeout_ms": 6_000,
        "headless": True,
        "production_claim": False,
    }
    reservation = HubBrowserTestRun.reserve(
        root=ROOT,
        registry_db=args.registry_db,
        task_id=TASK_ID,
        source_paths=SOURCE_PATHS,
        execution_profile=profile,
        environment=environment,
    )
    turn_host = local_non_loopback_ipv4()
    container_name = f"ananta-sfu-relay-{secrets.token_hex(6)}-turn-host"
    username = f"nat-{secrets.token_hex(8)}"
    credential = secrets.token_urlsafe(48)
    child_environment = os.environ.copy()
    measurements: list[dict[str, Any]] = []
    command_exit_code = -1
    image_id = ""
    failure_reason = ""
    cleanup_verified = False
    try:
        with locked_turn_udp_ports(turn_host) as (turn_port, relay_min, relay_max):
            blocked_port = reserve_port(socket_type=socket.SOCK_DGRAM, host=turn_host)
            run_command(
                build_host_turn_command(
                    container_name=container_name,
                    host=turn_host,
                    listening_port=turn_port,
                    relay_min_port=relay_min,
                    relay_max_port=relay_max,
                    username=username,
                    password=credential,
                ),
                env=child_environment,
                timeout=60,
            )
            wait_for_running_container(container_name, env=child_environment)
            image_id = verify_container_image(container_name, TURN_REPO_DIGEST, env=child_environment)
            with tempfile.TemporaryDirectory(prefix="ananta-peer-nat-") as measurement_dir:
                child_environment.update(
                    {
                        "ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON": json.dumps(
                            reservation.assignment, sort_keys=True
                        ),
                        "ANANTA_PEER_NAT_MEASUREMENT_DIR": measurement_dir,
                        "ANANTA_PEER_NAT_TURN_URL": f"turn:{turn_host}:{turn_port}",
                        "ANANTA_PEER_NAT_BLOCKED_TURN_URL": f"turn:{turn_host}:{blocked_port}",
                        "ANANTA_PEER_NAT_TURN_USERNAME": username,
                        "ANANTA_PEER_NAT_TURN_CREDENTIAL": credential,
                    }
                )
                with localhost_origin() as origin:
                    child_environment["ANANTA_PEER_NAT_ORIGIN"] = origin
                    completed = subprocess.run(
                        ("npx", "playwright", "test", "--config", "playwright.peer-nat-matrix.config.ts"),
                        cwd=FRONTEND,
                        env=child_environment,
                        check=False,
                        text=True,
                        timeout=180,
                    )
                command_exit_code = completed.returncode
                measurements = _measurements(Path(measurement_dir))
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        failure_reason = str(exc).split(":", 1)[0][:120] or type(exc).__name__
    finally:
        cleanup_verified = remove_owned_turn_container(container_name, env=child_environment)
    route_epoch_proof = _route_epoch_proof()
    succeeded = (
        not failure_reason
        and command_exit_code == 0
        and _matrix_complete(measurements)
        and all(route_epoch_proof.values())
        and cleanup_verified
    )
    payload = {
        "schema": "ananta.peer-nat-matrix-result.v1",
        "repository_revision": reservation.repository_revision,
        "status": "passed" if succeeded else "failed",
        "reason_codes": [] if succeeded else [failure_reason or "peer_nat_matrix_incomplete"],
        "measurements": measurements,
        "route_epoch_proof": route_epoch_proof,
        "execution_profile": profile,
        "host_environment": environment,
        "pinned_turn_image": TURN_REPO_DIGEST,
        "turn_image_id": image_id,
        "command_exit_code": command_exit_code,
        "cleanup_verified": cleanup_verified,
        "human_intervention_required": False,
    }
    evidence = reservation.complete(payload, succeeded=succeeded)
    report = {**payload, "evidence": evidence}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason_codes": report["reason_codes"]}, sort_keys=True))
    return 0 if succeeded and not evidence["production_release_eligible"] else 1


def _measurements(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def _matrix_complete(measurements: list[dict[str, Any]]) -> bool:
    if {row.get("engine") for row in measurements} != EXPECTED_ENGINES:
        return False
    for row in measurements:
        scenarios = row.get("scenarios")
        if not isinstance(scenarios, dict):
            return False
        if scenarios.get("turnUdp", {}).get("candidateType") != "relay":
            return False
        if scenarios.get("turnUdp", {}).get("relayProtocol") != "udp":
            return False
        if scenarios.get("turnTcp", {}).get("relayProtocol") != "tcp":
            return False
        switch = scenarios.get("networkSwitch", {})
        if (
            switch.get("before", {}).get("relayProtocol") != "udp"
            or switch.get("after", {}).get("relayProtocol") != "tcp"
            or switch.get("connectionGenerationAdvance") != 1
        ):
            return False
        if scenarios.get("blockedTurn", {}).get("outcome") != "turn_unreachable_bounded":
            return False
    return True


def _route_epoch_proof() -> dict[str, bool]:
    key = b"peer-nat-gate-hub-signing-key-32"
    initial = _lease(OverlayEpochs(7, 11, 3, 5), lease_id="lease-before", nonce="nonce-before").sign(key)
    switched = replace(
        initial,
        lease_id="lease-after",
        epochs=OverlayEpochs(7, 11, 4, 5),
        nonce="nonce-after",
        signature="",
    ).sign(key)
    switched.epochs.assert_successor(initial.epochs, change="route")
    switched.verify(
        key,
        expected_hub_key_id="hub-nat-gate",
        now="2026-09-05T00:00:30Z",
        tenant_id="tenant-nat-gate",
        room_id="room-nat-gate",
        publication_id="publication-nat-gate",
        child_peer_id="child-nat-gate",
        minimum_epochs=switched.epochs,
    )
    stale_rejected = False
    try:
        initial.verify(
            key,
            expected_hub_key_id="hub-nat-gate",
            now="2026-09-05T00:00:30Z",
            tenant_id="tenant-nat-gate",
            room_id="room-nat-gate",
            publication_id="publication-nat-gate",
            child_peer_id="child-nat-gate",
            minimum_epochs=switched.epochs,
        )
    except ValueError as exc:
        stale_rejected = str(exc) == "peer_overlay_lease_epoch_stale"
    return {
        "route_epoch_advanced": switched.epochs.route == initial.epochs.route + 1,
        "membership_epoch_preserved": switched.epochs.membership == initial.epochs.membership,
        "key_epoch_preserved": switched.epochs.key == initial.epochs.key,
        "stale_route_rejected": stale_rejected,
    }


def _lease(epochs: OverlayEpochs, *, lease_id: str, nonce: str) -> PeerRouteLease:
    return PeerRouteLease(
        version=1,
        lease_id=lease_id,
        tenant_id="tenant-nat-gate",
        room_id="room-nat-gate",
        publication_id="publication-nat-gate",
        child_peer_id="child-nat-gate",
        primary_parent_id="parent-nat-gate",
        backup_parent_id=None,
        epochs=epochs,
        capabilities=(OverlayCapability.DATA_RELAY, OverlayCapability.TURN),
        traffic_classes=(OverlayTrafficClass.CONTROL, OverlayTrafficClass.EVENT),
        max_hops=2,
        issued_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-05T00:01:00Z",
        nonce=nonce,
        hub_key_id="hub-nat-gate",
    )


if __name__ == "__main__":
    raise SystemExit(main())
