#!/usr/bin/env python3
"""Fail-closed release gate for the optional semantic-media SFU."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.semantic_media_program_evidence import canonical_sha256, source_hash

try:
    from scripts.e2e.semantic_media_group_e2e import expected_group_evidence_binding
    from scripts.e2e.semantic_sfu_failover_e2e import recompute_live_failover_evidence
except ModuleNotFoundError:  # Direct execution sets scripts/ as sys.path[0].
    from e2e.semantic_media_group_e2e import expected_group_evidence_binding
    from e2e.semantic_sfu_failover_e2e import recompute_live_failover_evidence

DEFAULT_SPIKE = ROOT / "artifacts/domain/semantic-sfu-three-peer.json"
DEFAULT_LOAD = ROOT / "artifacts/domain/semantic-sfu-load.json"
DEFAULT_FAILOVER = ROOT / "artifacts/domain/semantic-sfu-live-failover.json"
DEFAULT_GROUP = ROOT / "artifacts/e2e/semantic-media-group-report.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/semantic-sfu.json"
DIGEST = "sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
TURN_REFERENCE = (
    "coturn/coturn:4.6.3-r3@sha256:"
    "71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888"
)
SFU_SOURCE_PATHS = (
    ".env.example",
    "agent/db_models/semantic_media.py",
    "agent/repositories/semantic_sfu_admission_repository.py",
    "agent/routes/semantic_sfu_admission.py",
    "agent/services/media_topology_policy.py",
    "agent/services/semantic_fanout_coordination_service.py",
    "agent/services/semantic_sfu_admission_service.py",
    "agent/services/semantic_sfu_group_key_service.py",
    "agent/services/webrtc_group_key_authorization_service.py",
    "docker-compose.semantic-media.yml",
    "config/livekit.semantic-media.yaml",
    "docs/decisions/ADR-semantic-media-sfu.md",
    "docs/operations/semantic-media-sfu.md",
    "frontend-angular/package-lock.json",
    "frontend-angular/package.json",
    "frontend-angular/src/app/services/livekit-sfu-room.adapter.ts",
    "frontend-angular/src/app/services/livekit-sfu-transport.service.ts",
    "frontend-angular/src/app/services/media-e2ee-transform.service.ts",
    "frontend-angular/src/app/services/semantic-sfu-group-key-api.service.ts",
    "frontend-angular/src/app/services/semantic-receiver-path.service.ts",
    "frontend-angular/src/app/services/semantic-media-transport-state-machine.ts",
    "frontend-angular/src/app/services/sfu-media-frame-crypto.service.ts",
    "frontend-angular/src/app/services/webrtc-media-health.service.ts",
    "frontend-angular/src/app/services/webrtc-ordinary-health-monitor.service.ts",
    "frontend-angular/src/app/services/webrtc-session.service.ts",
    "frontend-angular/src/app/services/webrtc-group-key.service.ts",
    "migrations/versions/e6f7a8b9c0d1_add_semantic_sfu_admission_state.py",
    "schemas/webrtc/media_publication.v1.json",
    "schemas/webrtc/media_subscription.v1.json",
    "scripts/run_semantic_sfu_gate.py",
    "scripts/e2e/semantic_sfu_group_e2e.py",
    "scripts/e2e/semantic_media_group_e2e.py",
    "scripts/e2e/semantic_media_packet_capture.py",
    "scripts/e2e/semantic_sfu_failover_e2e.py",
    "scripts/e2e/semantic_sfu_hub_e2e.py",
    "scripts/e2e/sfu_broadcast_harness.py",
    "scripts/e2e/sfu_broadcast_browser_e2e.py",
    "scripts/run_sfu_broadcast_fuzz_gate.py",
    "scripts/run_sfu_broadcast_security_gate.py",
    "scripts/sfu_broadcast_gate_common.py",
    "config/test-profiles/sfu-broadcast/acceptance.v1.json",
    "config/test-profiles/sfu-broadcast/browser-matrix.v1.json",
    "scripts/spikes/semantic_sfu_failover.mjs",
    "scripts/spikes/semantic_sfu_three_peer.mjs",
)


def evidence_binding(
    spike: dict[str, Any],
    load: dict[str, Any],
    failover: dict[str, Any],
    group: dict[str, Any],
    *,
    root: Path = ROOT,
) -> tuple[str, str]:
    """Bind a gate run to the reviewed implementation and raw live evidence."""

    return (
        source_hash(root, SFU_SOURCE_PATHS),
        canonical_sha256(
            {
                "client_version": "2.20.1",
                "load_sha256": canonical_sha256(load),
                "failover_sha256": canonical_sha256(failover),
                "group_sha256": canonical_sha256(group),
                "server_digest": DIGEST,
                "spike_sha256": canonical_sha256(spike),
            }
        ),
    )


def recompute_evidence(
    spike: dict[str, Any],
    load: dict[str, Any],
    failover: dict[str, Any],
    group: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if spike.get("schema") != "ananta.semantic-sfu-three-peer-spike.v1":
        reasons.append("spike_schema_invalid")
    pinned = spike.get("pinned") if isinstance(spike.get("pinned"), dict) else {}
    if pinned.get("server_digest") != DIGEST or pinned.get("server_version") != "1.13.1":
        reasons.append("spike_server_not_pinned")
    if pinned.get("client_version") != "2.20.1":
        reasons.append("spike_client_not_pinned")
    topology = spike.get("topology") if isinstance(spike.get("topology"), dict) else {}
    if topology != {"publishers": 1, "receivers": 2, "expected_publisher_uploads": 1}:
        reasons.append("spike_topology_invalid")
    engines = spike.get("engines") if isinstance(spike.get("engines"), list) else []
    if len({row.get("engine") for row in engines if isinstance(row, dict)}) < 2:
        reasons.append("two_browser_engines_missing")
    for engine in engines:
        if not isinstance(engine, dict):
            reasons.append("spike_engine_invalid")
            continue
        peers = engine.get("peers") if isinstance(engine.get("peers"), list) else []
        publisher = next((row for row in peers if row.get("identity") == "publisher"), None)
        receivers = [row for row in peers if str(row.get("identity", "")).startswith("receiver-")]
        probe = next((row for row in peers if row.get("identity") == "wrong-key-probe"), None)
        if not publisher or publisher.get("peer_connections") != 1 or publisher.get("outbound_video_streams") != 1:
            reasons.append(f"{engine.get('engine', 'unknown')}_publisher_upload_not_single")
        if len(receivers) != 2 or any(row.get("decoded_samples", 0) < 3 for row in receivers):
            reasons.append(f"{engine.get('engine', 'unknown')}_two_receivers_missing")
        if not probe or probe.get("inbound_video_bytes", 0) <= 0 or probe.get("decoded_samples") != 0:
            reasons.append(f"{engine.get('engine', 'unknown')}_e2ee_negative_probe_failed")
    if load.get("schema") != "ananta.semantic-sfu-load.v1" or load.get("pinned_server_digest") != DIGEST:
        reasons.append("load_schema_or_pin_invalid")
    if "not a broadcast/production capacity claim" not in str(load.get("scope")):
        reasons.append("load_scope_overclaimed")
    thresholds = load.get("thresholds") if isinstance(load.get("thresholds"), dict) else {}
    levels = load.get("levels") if isinstance(load.get("levels"), list) else []
    if {row.get("participants") for row in levels if isinstance(row, dict)} != {2, 4}:
        reasons.append("load_levels_missing")
    for level in levels:
        if not isinstance(level, dict):
            reasons.append("load_level_invalid")
            continue
        if level.get("samples", 0) < 3:
            reasons.append("load_samples_insufficient")
        if level.get("cpu_percent", {}).get("max", float("inf")) > thresholds.get("max_cpu_percent", 0):
            reasons.append("load_cpu_exceeded")
        if level.get("memory_bytes", {}).get("max", 2**63) > thresholds.get("max_memory_bytes", 0):
            reasons.append("load_memory_exceeded")
        latency = level.get("scenario_completion_latency_ms", {})
        if latency.get("p99", 2**63) > thresholds.get("max_completion_p99_ms", 0):
            reasons.append("load_p99_exceeded")
        if level.get("dropped_video_frames", {}).get("max", 2**63) > thresholds.get("max_dropped_video_frames", 0):
            reasons.append("load_drops_exceeded")
        if level.get("publisher_ingress_bytes", {}).get("min", 0) <= 0:
            reasons.append("load_ingress_missing")
        if level.get("receiver_egress_bytes", {}).get("min", 0) <= 0:
            reasons.append("load_egress_missing")
    live_failover_reasons = recompute_live_failover_evidence(failover)
    reasons.extend(live_failover_reasons)
    live_failover_verified = not live_failover_reasons
    if failover.get("external_live_failover_verified") is not live_failover_verified:
        reasons.append("live_failover_report_decision_stale")
    if failover.get("verdict") != ("pass" if live_failover_verified else "fail"):
        reasons.append("live_failover_report_verdict_inconsistent")
    group_measurements = group.get("measurements") if isinstance(group.get("measurements"), dict) else {}
    if (
        group.get("schema") != "ananta.semantic-media-gate-evidence.v1"
        or group.get("gate_id") != "ASMP-QA-006"
        or group.get("status") != "passed"
        or group.get("reason_codes") != []
    ):
        reasons.append("group_capture_evidence_invalid")
    expected_group_source, expected_group_config = expected_group_evidence_binding()
    if (
        group.get("source_sha256") != expected_group_source
        or group.get("config_sha256") != expected_group_config
    ):
        reasons.append("group_capture_evidence_source_stale")
    capture_checks = {
        "sfu_boundary_capture_verified": True,
        "turn_boundary_capture_verified": True,
        "turn_relay_verified": True,
    }
    if any(group_measurements.get(key) is not value for key, value in capture_checks.items()):
        reasons.append("sfu_turn_capture_missing")
    if (
        group_measurements.get("sfu_boundary_packet_count", 0) < 1
        or group_measurements.get("turn_boundary_packet_count", 0) < 1
        or group_measurements.get("turn_relay_engine_count", 0) < 2
        or group_measurements.get("turn_relay_scenario_count", 0) < 2
    ):
        reasons.append("sfu_turn_live_traffic_missing")
    forbidden_capture_matches = (
        "sfu_boundary_known_marker_matches",
        "sfu_boundary_credential_matches",
        "turn_boundary_known_marker_matches",
        "turn_boundary_credential_matches",
    )
    if any(group_measurements.get(key) != 0 for key in forbidden_capture_matches):
        reasons.append("sfu_turn_plaintext_probe_failed")
    return sorted(set(reasons))


def static_reasons(root: Path = ROOT) -> list[str]:
    reasons: list[str] = []
    compose = (root / "docker-compose.semantic-media.yml").read_text(encoding="utf-8")
    livekit_config = (root / "config/livekit.semantic-media.yaml").read_text(encoding="utf-8")
    package = json.loads((root / "frontend-angular/package.json").read_text(encoding="utf-8"))
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    operations = (root / "docs/operations/semantic-media-sfu.md").read_text(encoding="utf-8")
    required_compose = [
        f"livekit/livekit-server@{DIGEST}",
        'profiles: ["semantic-media-sfu"]',
        "read_only: true",
        'cap_drop: ["ALL"]',
        "no-new-privileges:true",
        "healthcheck:",
        "stop_grace_period:",
        TURN_REFERENCE,
        'profiles: ["semantic-media-turn-gate"]',
        'cap_add: ["NET_BIND_SERVICE"]',
    ]
    if any(value not in compose for value in required_compose):
        reasons.append("compose_hardening_incomplete")
    if "turn:\n  enabled: true\n  udp_port: 3478" not in livekit_config:
        reasons.append("embedded_turn_udp_not_configured")
    if "\n  worker" in compose.lower():
        reasons.append("worker_attached_to_sfu_compose")
    if package.get("dependencies", {}).get("livekit-client") != "2.20.1":
        reasons.append("livekit_client_not_exact")
    if "ANANTA_SEMANTIC_MEDIA_SFU_ENABLED=false" not in env_example:
        reasons.append("sfu_default_not_disabled")
    if "test-side Hub authority simulator" in operations or "concurrent validator schedules" not in operations:
        reasons.append("productive_hub_failover_evidence_not_documented")
    required = [
        "agent/services/semantic_sfu_admission_service.py",
        "agent/repositories/semantic_sfu_admission_repository.py",
        "agent/services/semantic_sfu_group_key_service.py",
        "frontend-angular/src/app/services/livekit-sfu-transport.service.ts",
        "frontend-angular/src/app/services/media-e2ee-transform.service.ts",
        "frontend-angular/src/app/services/semantic-sfu-group-key-api.service.ts",
        "frontend-angular/src/app/services/semantic-receiver-path.service.ts",
        "agent/services/media_topology_policy.py",
        "agent/services/semantic_fanout_coordination_service.py",
        "frontend-angular/src/app/services/sfu-media-frame-crypto.service.ts",
        "frontend-angular/src/app/services/semantic-media-transport-state-machine.ts",
        "frontend-angular/src/app/services/webrtc-group-key.service.ts",
        "schemas/webrtc/media_publication.v1.json",
        "schemas/webrtc/media_subscription.v1.json",
        "scripts/e2e/semantic_sfu_failover_e2e.py",
        "scripts/e2e/semantic_sfu_hub_e2e.py",
        "scripts/e2e/semantic_media_group_e2e.py",
        "scripts/e2e/semantic_media_packet_capture.py",
        "scripts/e2e/sfu_broadcast_harness.py",
        "scripts/e2e/sfu_broadcast_browser_e2e.py",
        "scripts/run_sfu_broadcast_fuzz_gate.py",
        "scripts/run_sfu_broadcast_security_gate.py",
        "config/test-profiles/sfu-broadcast/acceptance.v1.json",
        "config/test-profiles/sfu-broadcast/browser-matrix.v1.json",
        "scripts/spikes/semantic_sfu_failover.mjs",
        "docs/decisions/ADR-semantic-media-sfu.md",
    ]
    if any(not (root / path).is_file() for path in required):
        reasons.append("required_sfu_source_missing")
    return reasons


def run_tests() -> list[dict[str, Any]]:
    commands = [
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "agent/services/semantic_sfu_admission_service.py",
            "agent/routes/semantic_sfu_admission.py",
            "agent/services/media_topology_policy.py",
            "agent/services/semantic_fanout_coordination_service.py",
            "scripts/e2e/semantic_sfu_group_e2e.py",
            "scripts/e2e/semantic_media_group_e2e.py",
            "scripts/e2e/semantic_media_packet_capture.py",
            "scripts/e2e/semantic_sfu_failover_e2e.py",
            "scripts/e2e/semantic_sfu_hub_e2e.py",
            "tests/test_semantic_sfu_admission.py",
            "tests/test_semantic_sfu_admission_repository.py",
            "tests/test_semantic_sfu_admission_migration.py",
            "tests/test_semantic_sfu_group_keys.py",
            "tests/test_media_topology_policy.py",
            "tests/test_semantic_sfu_live_runner.py",
            "tests/chaos/test_semantic_sfu_failover.py",
            "tests/integration/test_semantic_sfu_mixed_mode.py",
            "tests/test_sfu_broadcast_gate_foundation.py",
            "tests/fuzz/test_sfu_broadcast_contract_fuzz.py",
            "tests/fuzz/test_sfu_broadcast_state_machine_fuzz.py",
            "tests/security/test_sfu_broadcast_gate_security.py",
        ],
        ["node", "--check", "scripts/spikes/semantic_sfu_failover.mjs"],
        ["node", "--check", "scripts/spikes/semantic_sfu_three_peer.mjs"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_semantic_sfu_admission.py",
            "tests/test_media_topology_policy.py",
            "tests/test_semantic_sfu_live_runner.py",
            "tests/e2e/test_semantic_sfu_spike.py",
            "tests/chaos/test_semantic_sfu_failover.py",
            "tests/integration/test_semantic_sfu_mixed_mode.py",
        ],
        [
            "npx",
            "vitest",
            "run",
            "src/app/services/webrtc-media-session.service.spec.ts",
            "src/app/services/webrtc-media-publication.service.spec.ts",
            "src/app/services/webrtc-media-health.service.spec.ts",
            "src/app/services/webrtc-ordinary-health-monitor.service.spec.ts",
            "src/app/services/semantic-media-transport-state-machine.spec.ts",
            "src/app/services/sfu-media-frame-crypto.service.spec.ts",
            "src/app/services/livekit-sfu-transport.service.spec.ts",
            "src/app/services/media-e2ee-transform.service.spec.ts",
            "src/app/services/semantic-receiver-path.service.spec.ts",
        ],
    ]
    results: list[dict[str, Any]] = []
    for command in commands:
        cwd = ROOT / "frontend-angular" if command[0] == "npx" else ROOT
        env = dict(os.environ)
        if command[:3] == [sys.executable, "-m", "pytest"]:
            env["RUN_INTEGRATION_TESTS"] = "1"
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
        recorded_command = ["python", *command[1:]] if command[0] == sys.executable else command
        results.append({"command": " ".join(recorded_command), "exit_code": result.returncode})
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spike", type=Path, default=DEFAULT_SPIKE)
    parser.add_argument("--load", type=Path, default=DEFAULT_LOAD)
    parser.add_argument("--failover", type=Path, default=DEFAULT_FAILOVER)
    parser.add_argument("--group", type=Path, default=DEFAULT_GROUP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    try:
        spike = json.loads(args.spike.read_text(encoding="utf-8"))
        load = json.loads(args.load.read_text(encoding="utf-8"))
        failover = json.loads(args.failover.read_text(encoding="utf-8"))
        group = json.loads(args.group.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        spike, load, failover, group = {}, {}, {}, {}
    reasons = recompute_evidence(spike, load, failover, group) + static_reasons()
    tests = [] if args.skip_tests else run_tests()
    if any(row["exit_code"] != 0 for row in tests):
        reasons.append("focused_tests_failed")
    reasons = sorted(set(reasons))
    source_digest, config_digest = evidence_binding(spike, load, failover, group)
    report = {
        "schema": "ananta.semantic-sfu-release-gate.v1",
        "gate": "semantic-sfu",
        "source_sha256": source_digest,
        "config_sha256": config_digest,
        "evidence_recomputed": True,
        "tests": tests,
        "measurements": {
            "external_live_failover_verified": not recompute_live_failover_evidence(failover),
            "live_failover_browser_engine_count": len(failover.get("engines", []))
            if isinstance(failover.get("engines"), list)
            else 0,
            "sfu_boundary_packet_count": int(
                dict(group.get("measurements") or {}).get("sfu_boundary_packet_count", 0)
            ),
            "turn_boundary_packet_count": int(
                dict(group.get("measurements") or {}).get("turn_boundary_packet_count", 0)
            ),
            "turn_relay_engine_count": int(
                dict(group.get("measurements") or {}).get("turn_relay_engine_count", 0)
            ),
        },
        "reasons": reasons,
        "verdict": "pass" if not reasons else "fail",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "verdict": report["verdict"], "reasons": reasons}))
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
