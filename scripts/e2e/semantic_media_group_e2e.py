#!/usr/bin/env python3
"""Run or explicitly mark unavailable the four-participant mixed-mode E2E gate."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import GateEvidence, canonical_sha256, write_report

try:
    from scripts.e2e.semantic_media_packet_capture import (
        CAPTURE_IMAGE,
        capture_measurements,
        start_container_capture,
        stop_capture,
    )
except ModuleNotFoundError:
    try:
        from e2e.semantic_media_packet_capture import (
            CAPTURE_IMAGE,
            capture_measurements,
            start_container_capture,
            stop_capture,
        )
    except ModuleNotFoundError:
        from semantic_media_packet_capture import (
            CAPTURE_IMAGE,
            capture_measurements,
            start_container_capture,
            stop_capture,
        )

try:
    from scripts.e2e.semantic_media_e2e_report import ROOT, run_playwright_gate
except ModuleNotFoundError:  # Direct execution sets scripts/e2e as sys.path[0].
    try:
        from e2e.semantic_media_e2e_report import ROOT, run_playwright_gate
    except ModuleNotFoundError:
        from semantic_media_e2e_report import ROOT, run_playwright_gate

try:
    from scripts.e2e.semantic_sfu_group_e2e import COMPOSE, inspect_image, run_command, run_spike
except ModuleNotFoundError:
    try:
        from e2e.semantic_sfu_group_e2e import COMPOSE, inspect_image, run_command, run_spike
    except ModuleNotFoundError:
        from semantic_sfu_group_e2e import COMPOSE, inspect_image, run_command, run_spike

try:
    from scripts.e2e.semantic_sfu_failover_e2e import (
        DEFAULT_OUTPUT as DEFAULT_FAILOVER_OUTPUT,
    )
    from scripts.e2e.semantic_sfu_failover_e2e import (
        execute as run_live_failover,
    )
    from scripts.e2e.semantic_sfu_failover_e2e import (
        recompute_live_failover_evidence,
    )
except ModuleNotFoundError:
    try:
        from e2e.semantic_sfu_failover_e2e import (
            DEFAULT_OUTPUT as DEFAULT_FAILOVER_OUTPUT,
        )
        from e2e.semantic_sfu_failover_e2e import (
            execute as run_live_failover,
        )
        from e2e.semantic_sfu_failover_e2e import (
            recompute_live_failover_evidence,
        )
    except ModuleNotFoundError:
        from semantic_sfu_failover_e2e import (
            DEFAULT_OUTPUT as DEFAULT_FAILOVER_OUTPUT,
        )
        from semantic_sfu_failover_e2e import (
            execute as run_live_failover,
        )
        from semantic_sfu_failover_e2e import (
            recompute_live_failover_evidence,
        )

TURN_IMAGE = (
    "coturn/coturn@sha256:"
    "71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888"
)
TURN_USER = "ananta-turn-gate"
TURN_CREDENTIAL = "ananta-turn-gate-fixture-32-bytes"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/e2e/semantic-media-group-report.json")
    args = parser.parse_args()
    if not args.execute_live:
        evidence = run_playwright_gate(
            gate_id="ASMP-QA-006", spec="semantic-media-group.spec.ts", execute_live=False,
        )
    else:
        environment = dict(os.environ)
        environment.update(
            {
                "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY": "ananta-local-group-gate",
                "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET": secrets.token_urlsafe(48),
                "ANANTA_SEMANTIC_MEDIA_SFU_ENABLED": "true",
                "ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL": "ws://127.0.0.1:7880",
                "ANANTA_SEMANTIC_MEDIA_TURN_GATE_USER": TURN_USER,
                "ANANTA_SEMANTIC_MEDIA_TURN_GATE_PASSWORD": TURN_CREDENTIAL,
                "E2E_PORT": str(_available_loopback_port()),
            }
        )
        compose = [
            "docker", "compose", "--project-name", f"ananta-semantic-group-{secrets.token_hex(6)}",
            "-f", str(COMPOSE), "--profile", "semantic-media-sfu",
            "--profile", "semantic-media-turn-gate",
        ]
        sfu_capture_name = f"ananta-semantic-sfu-capture-{secrets.token_hex(6)}"
        turn_capture_name = f"ananta-semantic-turn-capture-{secrets.token_hex(6)}"
        try:
            inspect_image()
            run_command(
                [*compose, "up", "-d", "--wait", "semantic-media-sfu", "semantic-media-turn-gate"],
                env=environment,
                timeout=120,
            )
            environment["ANANTA_SEMANTIC_MEDIA_TURN_GATE_URL"] = _turn_gate_url(
                compose,
                environment,
            )
            with tempfile.TemporaryDirectory(prefix="ananta-semantic-sfu-capture-") as temporary:
                capture_dir = Path(temporary)
                capture_dir.chmod(0o777)
                sfu_capture_path = capture_dir / "sfu-boundary.pcap"
                turn_capture_path = capture_dir / "turn-boundary.pcap"
                relay_report_path = capture_dir / "turn-relay-report.json"
                _start_capture(
                    compose=compose,
                    environment=environment,
                    capture_name=sfu_capture_name,
                    capture_dir=capture_dir,
                    capture_path=sfu_capture_path.name,
                    target_service="semantic-media-sfu",
                    capture_filter=("udp", "port", "7882", "or", "tcp", "port", "7881"),
                )
                _start_capture(
                    compose=compose,
                    environment=environment,
                    capture_name=turn_capture_name,
                    capture_dir=capture_dir,
                    capture_path=turn_capture_path.name,
                    target_service="semantic-media-turn-gate",
                    capture_filter=("udp", "port", "3478", "or", "udp", "portrange", "49160-49200"),
                )
                browser_evidence: GateEvidence | None = None
                relay_report: dict[str, object] | None = None
                failover_report: dict[str, Any] | None = None
                try:
                    browser_evidence = run_playwright_gate(
                        gate_id="ASMP-QA-006",
                        spec="semantic-media-group.spec.ts",
                        execute_live=True,
                        environment_overrides={
                            name: environment[name]
                            for name in (
                                "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY",
                                "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET",
                                "ANANTA_SEMANTIC_MEDIA_SFU_ENABLED",
                                "ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL",
                                "E2E_PORT",
                            )
                        },
                    )
                    relay_environment = dict(environment)
                    relay_environment["ANANTA_SEMANTIC_MEDIA_SFU_FORCE_RELAY"] = "1"
                    relay_report, _ = run_spike(
                        env=relay_environment,
                        output=relay_report_path,
                        engines="chromium,firefox",
                        receiver_count=2,
                        wrong_key=True,
                    )
                    failover_report, _failover_reasons = run_live_failover(DEFAULT_FAILOVER_OUTPUT)
                finally:
                    stop_capture(sfu_capture_name)
                    stop_capture(turn_capture_name)
                assert browser_evidence is not None
                assert relay_report is not None
                assert failover_report is not None
                evidence = _bind_capture_evidence(
                    browser_evidence,
                    {
                        **capture_measurements(sfu_capture_path, "sfu"),
                        **capture_measurements(turn_capture_path, "turn"),
                        **_relay_measurements(relay_report),
                    },
                    failover_report,
                )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason_code": str(exc)[:160] or type(exc).__name__,
                    },
                    sort_keys=True,
                )
            )
            baseline = run_playwright_gate(
                gate_id="ASMP-QA-006", spec="semantic-media-group.spec.ts", execute_live=False,
            )
            evidence = GateEvidence(
                gate_id=baseline.gate_id,
                status="failed",
                reason_codes=("live_sfu_stack_unavailable",),
                source_sha256=baseline.source_sha256,
                config_sha256=baseline.config_sha256,
                measurements={"executed_tests": 0, "passed_tests": 0, "failed_tests": 1, "browser_count": 0},
            )
        finally:
            for capture_name in (sfu_capture_name, turn_capture_name):
                subprocess.run(
                    ["docker", "rm", "-f", capture_name], cwd=ROOT,
                    check=False, timeout=30, capture_output=True,
                )
            subprocess.run(
                [*compose, "down", "--remove-orphans"], cwd=ROOT, env=environment,
                check=False, timeout=120, capture_output=True,
            )
    write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _turn_gate_url(compose: list[str], environment: dict[str, str]) -> str:
    container = run_command(
        [*compose, "ps", "-q", "semantic-media-turn-gate"],
        env=environment,
        timeout=30,
    ).stdout.strip()
    if not container:
        raise RuntimeError("turn_gate_target_missing")
    address = run_command(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container,
        ],
        env=environment,
        timeout=30,
    ).stdout.strip()
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise RuntimeError("turn_gate_address_invalid") from exc
    if parsed.version != 4 or not parsed.is_private:
        raise RuntimeError("turn_gate_address_invalid")
    return f"turn:{parsed}:3478?transport=udp"


def _start_capture(
    *,
    compose: list[str],
    environment: dict[str, str],
    capture_name: str,
    capture_dir: Path,
    capture_path: str,
    target_service: str,
    capture_filter: tuple[str, ...],
) -> None:
    container = run_command(
        [*compose, "ps", "-q", target_service],
        env=environment,
        timeout=30,
    ).stdout.strip()
    if not container:
        raise RuntimeError("sfu_capture_target_missing")
    start_container_capture(
        capture_name=capture_name,
        target_container=container,
        capture_dir=capture_dir,
        capture_path=capture_path,
        capture_filter=capture_filter,
    )


def _relay_measurements(report: dict[str, object]) -> dict[str, int | bool]:
    engines = report.get("engines")
    if (
        report.get("schema") != "ananta.semantic-sfu-three-peer-spike.v1"
        or report.get("transport_profile") != "turn_relay_required"
        or report.get("verdict") != "pass"
        or not isinstance(engines, list)
    ):
        raise RuntimeError("turn_relay_report_invalid")
    valid = [
        row
        for row in engines
        if isinstance(row, dict)
        and row.get("relay_required") is True
        and row.get("relay_selected") is True
        and row.get("verdict") == "pass"
    ]
    engine_names = {str(row.get("engine")) for row in valid}
    if engine_names != {"chromium", "firefox"}:
        raise RuntimeError("turn_relay_browser_coverage_missing")
    return {
        "turn_relay_verified": True,
        "turn_relay_engine_count": len(engine_names),
        "turn_relay_scenario_count": len(valid),
    }


def _bind_capture_evidence(
    browser: GateEvidence,
    capture: dict[str, int | bool],
    failover_report: Mapping[str, Any] | None = None,
) -> GateEvidence:
    reasons = list(browser.reason_codes)
    if (
        capture["sfu_boundary_known_marker_matches"] != 0
        or capture["sfu_boundary_credential_matches"] != 0
        or capture["turn_boundary_known_marker_matches"] != 0
        or capture["turn_boundary_credential_matches"] != 0
    ):
        reasons.append("sfu_boundary_known_plaintext_detected")
    failover_measurements = _failover_measurements(failover_report)
    if failover_report is None or recompute_live_failover_evidence(failover_report):
        reasons.append("group_live_failover_evidence_invalid")
    source_digest, config_digest = _group_binding(
        browser.source_sha256,
        browser.config_sha256,
    )
    return GateEvidence(
        gate_id=browser.gate_id,
        status="passed" if browser.status == "passed" and not reasons else "failed",
        reason_codes=tuple(sorted(set(reasons))),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements={**browser.measurements, **capture, **failover_measurements},
    )


def _failover_measurements(report: Mapping[str, Any] | None) -> dict[str, int | bool]:
    """Project only bounded, content-free restart and lease measurements."""

    document = report or {}
    engines = document.get("engines") if isinstance(document.get("engines"), list) else []
    rows = [row for row in engines if isinstance(row, Mapping)]
    runner = document.get("runner") if isinstance(document.get("runner"), Mapping) else {}
    compute_rows = [
        row.get("compute") if isinstance(row.get("compute"), Mapping) else {}
        for row in rows
    ]
    outage_rows = [
        row.get("outage") if isinstance(row.get("outage"), Mapping) else {}
        for row in rows
    ]
    recovery_rows = [
        row.get("recovery") if isinstance(row.get("recovery"), Mapping) else {}
        for row in rows
    ]
    return {
        "live_failover_verified": not recompute_live_failover_evidence(document),
        "live_failover_engine_count": len({str(row.get("engine")) for row in rows}),
        "sfu_restart_count": int(runner.get("restart_count") or 0),
        "hub_restart_count": int(runner.get("hub_restart_count") or 0),
        "persistent_admission_recovery_count": sum(
            item.get("persistent_admission_state_resumed") is True for item in recovery_rows
        ),
        "old_authorization_rejection_count": sum(
            int(item.get("old_authorization_rejected_count") or 0) for item in recovery_rows
        ),
        "fresh_admission_count": sum(
            int(item.get("fresh_admission_count") or 0) for item in recovery_rows
        ),
        "primary_replacement_count": sum(
            int(item.get("replacement_primary_lease_count") or 0) for item in compute_rows
        ),
        "validator_conflict_rejection_count": sum(
            int(item.get("validator_conflict_rejection_count") or 0) for item in compute_rows
        ),
        "duplicate_active_lease_count": sum(
            int(item.get("duplicate_active_lease_count") or 0) for item in compute_rows
        ),
        "hub_sole_lease_authority_count": sum(
            item.get("hub_remained_sole_lease_authority") is True for item in compute_rows
        ),
        "restart_ordinary_fallback_count": sum(
            item.get("controlled_mode") == "ordinary_audio_fallback" for item in outage_rows
        ),
    }


def expected_group_evidence_binding() -> tuple[str, str]:
    baseline = run_playwright_gate(
        gate_id="ASMP-QA-006",
        spec="semantic-media-group.spec.ts",
        execute_live=False,
    )
    return _group_binding(baseline.source_sha256, baseline.config_sha256)


def _group_binding(browser_source_sha256: str, browser_config_sha256: str) -> tuple[str, str]:
    source_digest = canonical_sha256(
        {
            "browser_source_sha256": browser_source_sha256,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "capture_source_sha256": hashlib.sha256(
                (ROOT / "scripts/e2e/semantic_media_packet_capture.py").read_bytes()
            ).hexdigest(),
            "relay_spike_sha256": hashlib.sha256(
                (ROOT / "scripts/spikes/semantic_sfu_three_peer.mjs").read_bytes()
            ).hexdigest(),
            "failover_runner_sha256": hashlib.sha256(
                (ROOT / "scripts/e2e/semantic_sfu_failover_e2e.py").read_bytes()
            ).hexdigest(),
            "failover_hub_sha256": hashlib.sha256(
                (ROOT / "scripts/e2e/semantic_sfu_hub_e2e.py").read_bytes()
            ).hexdigest(),
            "failover_spike_sha256": hashlib.sha256(
                (ROOT / "scripts/spikes/semantic_sfu_failover.mjs").read_bytes()
            ).hexdigest(),
        }
    )
    config_digest = canonical_sha256(
            {
                "browser_config_sha256": browser_config_sha256,
                "capture_image": CAPTURE_IMAGE,
                "capture_boundaries": ["sfu_network_namespace", "turn_network_namespace"],
                "turn_image": TURN_IMAGE,
                "live_failover_required": True,
            }
    )
    return source_digest, config_digest


if __name__ == "__main__":
    raise SystemExit(main())
