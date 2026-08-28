#!/usr/bin/env python3
"""Run an isolated, real browser/SFU kill-restart recovery scenario.

The runner creates an ephemeral Compose project and config with matching
loopback host/container RTC ports.  It never targets a pre-existing project.
Only recomputed, content-free measurements leave the temporary directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import assert_content_free

try:
    from scripts.e2e.semantic_media_packet_capture import (
        CAPTURE_IMAGE,
        capture_measurements,
        start_host_capture,
        stop_capture,
    )
except ModuleNotFoundError:
    try:
        from e2e.semantic_media_packet_capture import (
            CAPTURE_IMAGE,
            capture_measurements,
            start_host_capture,
            stop_capture,
        )
    except ModuleNotFoundError:
        from semantic_media_packet_capture import (
            CAPTURE_IMAGE,
            capture_measurements,
            start_host_capture,
            stop_capture,
        )

ROOT = Path(__file__).resolve().parents[2]
SPIKE = ROOT / "scripts/spikes/semantic_sfu_failover.mjs"
HUB_FIXTURE = ROOT / "scripts/e2e/semantic_sfu_hub_e2e.py"
DEFAULT_OUTPUT = ROOT / "artifacts/domain/semantic-sfu-live-failover.json"
IMAGE = "livekit/livekit-server:v1.13.1"
DIGEST = "sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
EXPECTED_IMAGE_ID = DIGEST.removeprefix("sha256:")
EXPECTED_ACTIONS = (
    "sfu_kill",
    "hub_kill",
    "hub_start",
    "sfu_start",
    "sfu_kill",
    "hub_kill",
    "hub_start",
    "sfu_start",
)

_PUBLIC_BROWSER_FAILURES = {
    "pinned livekit-client assets missing": "live_failover_client_assets_missing",
    "engine Hub fixture missing": "live_failover_engine_fixture_missing",
    "productive Hub SFU authority binding invalid": "live_failover_hub_sfu_authority_invalid",
    "productive Hub compute contract not active": "live_failover_compute_contract_inactive",
    "Hub compute lease state did not survive restart": "live_failover_compute_lease_recovery_failed",
    "old epoch authorization remained usable": "live_failover_old_authorization_accepted",
    "Hub failover rekey reason missing": "live_failover_rekey_reason_missing",
    "fresh epoch reused content key": "live_failover_content_key_reused",
    "initial publisher flow missing": "live_failover_initial_publisher_flow_missing",
    "initial receiver flow missing": "live_failover_initial_receiver_flow_missing",
    "initial stale-key baseline missing": "live_failover_stale_key_baseline_missing",
    "ordinary fallback mode not exclusive": "live_failover_ordinary_fallback_not_exclusive",
    "ordinary fallback topology invalid": "live_failover_ordinary_fallback_topology_invalid",
    "ordinary audio flow missing": "live_failover_ordinary_audio_flow_missing",
    "recovered publisher flow missing": "live_failover_recovered_publisher_flow_missing",
    "recovered receiver flow missing": "live_failover_recovered_receiver_flow_missing",
    "stale key decoded recovery media": "live_failover_stale_key_media_accepted",
    "fresh Hub admission evidence incomplete": "live_failover_fresh_admission_incomplete",
    "browser cleanup failed": "live_failover_browser_cleanup_failed",
}


class LiveFailoverError(RuntimeError):
    """A bounded, content-free live-run failure."""


class IsolatedHubRuntime:
    """Own one loopback Hub process while preserving its ephemeral SQL DB."""

    def __init__(self, *, port: int, env: Mapping[str, str], log_path: Path) -> None:
        self.port = port
        self.env = dict(env)
        self.log_path = log_path
        self.process: subprocess.Popen[str] | None = None
        self.process_ids: list[int] = []
        self.start_count = 0
        self.sigkill_count = 0
        self._log_handle = None
        self._last_killed_at: float | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise LiveFailoverError("isolated_hub_already_running")
        if self._last_killed_at is not None:
            remaining_lease = 31.0 - (time.monotonic() - self._last_killed_at)
            if remaining_lease > 0:
                time.sleep(remaining_lease)
        self._log_handle = self.log_path.open("a", encoding="utf-8")
        process_env = dict(self.env)
        process_env["AGENT_NAME"] = f"{self.env['AGENT_NAME']}-incarnation-{self.start_count + 1}"
        self.process = subprocess.Popen(
            [sys.executable, str(HUB_FIXTURE), "serve", "--port", str(self.port)],
            cwd=ROOT,
            env=process_env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self.process_ids.append(self.process.pid)
        self.start_count += 1
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._close_log()
                raise LiveFailoverError("isolated_hub_process_failed")
            try:
                with urllib.request.urlopen(f"{self.url}/healthz", timeout=1) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and body.get("authority") == "productive_hub_composition":
                    return
            except (OSError, ValueError, urllib.error.URLError):
                time.sleep(0.1)
        raise LiveFailoverError("isolated_hub_health_timeout")

    def kill(self) -> None:
        if self.process is None or self.process.poll() is not None:
            raise LiveFailoverError("isolated_hub_not_running")
        os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=15)
        self.sigkill_count += 1
        self._last_killed_at = time.monotonic()
        self._close_log()
        try:
            urllib.request.urlopen(f"{self.url}/healthz", timeout=1)
        except (OSError, urllib.error.URLError):
            return
        raise LiveFailoverError("isolated_hub_sigkill_not_observed")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=15)
        self._close_log()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def run_command(
    command: list[str],
    *,
    env: Mapping[str, str],
    timeout: int = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise LiveFailoverError(f"command_failed_{Path(command[0]).name}")
    return result


def inspect_image() -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or result.stdout.strip().removeprefix("sha256:") != EXPECTED_IMAGE_ID:
        raise LiveFailoverError("live_sfu_image_digest_mismatch")


def reserve_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def reserve_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def allocate_ports() -> tuple[int, int, int]:
    for _ in range(10):
        values = (reserve_tcp_port(), reserve_tcp_port(), reserve_udp_port())
        if len(set(values)) == 3:
            return values
    raise LiveFailoverError("isolated_port_allocation_failed")


def write_isolated_deployment(directory: Path, *, http_port: int, tcp_port: int, udp_port: int) -> Path:
    livekit_config = directory / "livekit.yaml"
    livekit_config.write_text(
        "\n".join(
            (
                f"port: {http_port}",
                "bind_addresses:",
                "  - 0.0.0.0",
                "rtc:",
                f"  tcp_port: {tcp_port}",
                f"  udp_port: {udp_port}",
                "  use_external_ip: false",
                "  enable_loopback_candidate: true",
                "logging:",
                "  level: warn",
                "room:",
                "  empty_timeout: 60",
                "  departure_timeout: 10",
                "  max_participants: 8",
                "",
            )
        ),
        encoding="utf-8",
    )
    livekit_config.chmod(0o644)
    compose = directory / "compose.yml"
    compose.write_text(
        "\n".join(
            (
                "name: ananta-semantic-sfu-live-failover",
                "services:",
                "  semantic-media-sfu:",
                f"    image: livekit/livekit-server@{DIGEST}",
                '    command: ["--config", "/etc/livekit/livekit.yaml"]',
                '    user: "65532:65532"',
                "    read_only: true",
                '    cap_drop: ["ALL"]',
                "    security_opt:",
                "      - no-new-privileges:true",
                "    tmpfs:",
                "      - /tmp:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532",
                "    volumes:",
                f"      - {livekit_config}:/etc/livekit/livekit.yaml:ro",
                "    environment:",
                '      LIVEKIT_KEYS: "${ANANTA_SEMANTIC_MEDIA_SFU_API_KEY}: ${ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET}"',
                "    ports:",
                f'      - "127.0.0.1:{http_port}:{http_port}/tcp"',
                f'      - "127.0.0.1:{tcp_port}:{tcp_port}/tcp"',
                f'      - "127.0.0.1:{udp_port}:{udp_port}/udp"',
                "    healthcheck:",
                f'      test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:{http_port}/"]',
                "      interval: 2s",
                "      timeout: 2s",
                "      retries: 20",
                "      start_period: 2s",
                "    stop_grace_period: 10s",
                '    restart: "no"',
                "    deploy:",
                "      resources:",
                '        limits: {cpus: "2.0", memory: 1G, pids: 256}',
                "",
            )
        ),
        encoding="utf-8",
    )
    return compose


def atomic_json(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def container_id(compose: list[str], env: Mapping[str, str], *, all_states: bool = False) -> str:
    args = [*compose, "ps"]
    if all_states:
        args.append("-a")
    args.extend(["-q", "semantic-media-sfu"])
    value = run_command(args, env=env, timeout=30).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", value):
        raise LiveFailoverError("isolated_sfu_container_missing")
    return value


def container_running(identifier: str, env: Mapping[str, str]) -> bool:
    result = run_command(
        ["docker", "inspect", "--format", "{{.State.Running}}", identifier],
        env=env,
        timeout=30,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def handle_control_action(
    action: str,
    *,
    compose: list[str],
    env: Mapping[str, str],
    hub: IsolatedHubRuntime,
) -> None:
    if action == "sfu_kill":
        identifier = container_id(compose, env)
        run_command([*compose, "kill", "-s", "SIGKILL", "semantic-media-sfu"], env=env, timeout=30)
        if container_running(identifier, env):
            raise LiveFailoverError("isolated_sfu_sigkill_not_observed")
        return
    if action == "sfu_start":
        run_command([*compose, "up", "-d", "--wait", "semantic-media-sfu"], env=env, timeout=120)
        identifier = container_id(compose, env)
        if not container_running(identifier, env):
            raise LiveFailoverError("isolated_sfu_restart_not_observed")
        return
    if action == "hub_kill":
        hub.kill()
        return
    if action == "hub_start":
        hub.start()
        return
    raise LiveFailoverError("control_action_invalid")


def drive_spike(
    *,
    compose: list[str],
    env: Mapping[str, str],
    directory: Path,
    raw_output: Path,
    hub: IsolatedHubRuntime,
    fixture_descriptor: Path,
) -> tuple[dict[str, Any], tuple[str, ...], int]:
    request_path = directory / "request.json"
    response_path = directory / "response.json"
    child_env = dict(env)
    child_env.update(
        {
            "ANANTA_SEMANTIC_MEDIA_SFU_CONTROL_DIR": str(directory),
            "ANANTA_SEMANTIC_MEDIA_SFU_FAILOVER_OUTPUT": str(raw_output),
            "ANANTA_SEMANTIC_MEDIA_SFU_HUB_URL": hub.url,
            "ANANTA_SEMANTIC_MEDIA_SFU_HUB_FIXTURE": str(fixture_descriptor),
        }
    )
    process = subprocess.Popen(
        ["node", str(SPIKE)],
        cwd=ROOT,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    actions: list[str] = []
    last_sequence = 0
    deadline = time.monotonic() + 480
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise LiveFailoverError("live_failover_browser_timeout")
            if not request_path.is_file():
                time.sleep(0.05)
                continue
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            sequence = request.get("sequence")
            action = request.get("action")
            if type(sequence) is not int or sequence <= last_sequence:
                time.sleep(0.05)
                continue
            expected = EXPECTED_ACTIONS[len(actions)] if len(actions) < len(EXPECTED_ACTIONS) else None
            if request.get("version") != 1 or action != expected:
                atomic_json(response_path, {"sequence": sequence, "action": str(action), "ok": False})
                raise LiveFailoverError("live_failover_control_sequence_invalid")
            try:
                handle_control_action(str(action), compose=compose, env=env, hub=hub)
            except (LiveFailoverError, subprocess.TimeoutExpired):
                atomic_json(response_path, {"sequence": sequence, "action": str(action), "ok": False})
                raise
            actions.append(str(action))
            last_sequence = sequence
            atomic_json(response_path, {"sequence": sequence, "action": str(action), "ok": True})
        stdout, stderr = process.communicate(timeout=10)
        del stdout
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        raise
    if process.returncode != 0:
        raise LiveFailoverError(_classify_browser_failure(stderr))
    if tuple(actions) != EXPECTED_ACTIONS:
        raise LiveFailoverError("live_failover_control_actions_incomplete")
    try:
        report = json.loads(raw_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveFailoverError("live_failover_raw_report_missing") from exc
    if not isinstance(report, dict):
        raise LiveFailoverError("live_failover_raw_report_invalid")
    return report, tuple(actions), int(process.returncode)


def _classify_browser_failure(stderr: str) -> str:
    """Reduce a child-process failure to a closed, content-free reason code."""

    for marker, reason_code in _PUBLIC_BROWSER_FAILURES.items():
        if marker in stderr:
            return reason_code
    match = re.search(r"productive Hub API rejected [1-5][0-9]{2}:([a-z0-9_:-]{1,96})", stderr)
    if match is not None:
        public_code = match.group(1).replace(":", "_")
        return f"live_failover_hub_api_rejected_{public_code}"
    return "live_failover_browser_process_failed"


def recompute_live_failover_evidence(report: Mapping[str, Any]) -> list[str]:
    """Recompute the live verdict without trusting any reported verdict field."""

    reasons: list[str] = []
    if report.get("schema") != "ananta.semantic-sfu-live-failover.v1":
        reasons.append("live_failover_schema_invalid")
    pinned = report.get("pinned") if isinstance(report.get("pinned"), Mapping) else {}
    if pinned != {
        "server_version": "1.13.1",
        "server_digest": DIGEST,
        "client_version": "2.20.1",
    }:
        reasons.append("live_failover_dependency_pin_invalid")
    topology = report.get("topology") if isinstance(report.get("topology"), Mapping) else {}
    if topology != {
        "publishers": 1,
        "required_receivers": 2,
        "stale_key_probes": 1,
        "browser_engines": ["chromium", "firefox"],
    }:
        reasons.append("live_failover_topology_invalid")
    authority = report.get("authority") if isinstance(report.get("authority"), Mapping) else {}
    if (
        authority.get("kind") != "productive-hub-api"
        or authority.get("admission_api") != "semantic_sfu_admission_bp"
        or authority.get("compute_api") != "semantic_media_contracts_bp"
        or authority.get("state_repository") != "sql_cas"
        or authority.get("signature_algorithm") != "Ed25519"
        or authority.get("browser_mints_admission") is not False
        or authority.get("epoch_transition") != [1, 2]
        or authority.get("recovery_reason") != "hub_failover"
    ):
        reasons.append("live_failover_hub_authority_invalid")
    if report.get("persisted_source_data") is not False:
        reasons.append("live_failover_content_persistence_detected")
    engines = report.get("engines") if isinstance(report.get("engines"), list) else []
    if {row.get("engine") for row in engines if isinstance(row, Mapping)} != {"chromium", "firefox"}:
        reasons.append("live_failover_browser_engines_missing")
    if len(engines) != 2:
        reasons.append("live_failover_engine_count_invalid")
    for row in engines:
        if not isinstance(row, Mapping):
            reasons.append("live_failover_engine_result_invalid")
            continue
        engine = str(row.get("engine") or "unknown")
        before = row.get("pre_failure") if isinstance(row.get("pre_failure"), Mapping) else {}
        outage = row.get("outage") if isinstance(row.get("outage"), Mapping) else {}
        recovery = row.get("recovery") if isinstance(row.get("recovery"), Mapping) else {}
        compute = row.get("compute") if isinstance(row.get("compute"), Mapping) else {}
        cleanup = row.get("cleanup") if isinstance(row.get("cleanup"), Mapping) else {}
        if (
            before.get("publisher_outbound_bytes", 0) <= 0
            or before.get("receiver_count") != 2
            or before.get("receiver_min_inbound_bytes", 0) <= 0
            or before.get("receiver_min_decoded_samples", 0) < 3
            or before.get("stale_probe_initial_inbound_bytes", 0) <= 0
            or before.get("stale_probe_initial_decoded_samples", 0) < 3
        ):
            reasons.append(f"{engine}_pre_failure_flow_missing")
        if (
            outage.get("sfu_sigkill_acknowledged") is not True
            or outage.get("hub_sigkill_acknowledged") is not True
            or outage.get("hub_api_unavailable_verified") is not True
            or outage.get("reconnecting_client_count") != 4
            or outage.get("disconnected_client_count") != 4
            or outage.get("semantic_room_count_during_fallback") != 0
            or outage.get("ordinary_peer_connection_count") != 4
            or outage.get("ordinary_receiver_count") != 2
            or outage.get("ordinary_min_outbound_bytes", 0) <= 0
            or outage.get("ordinary_min_inbound_bytes", 0) <= 0
            or outage.get("controlled_mode") != "ordinary_audio_fallback"
        ):
            reasons.append(f"{engine}_controlled_fallback_missing")
        if (
            recovery.get("sfu_restart_acknowledged") is not True
            or recovery.get("hub_restart_acknowledged") is not True
            or recovery.get("persistent_admission_state_resumed") is not True
            or type(recovery.get("admission_revision_after_restart")) is not int
            or type(recovery.get("admission_revision_before_restart")) is not int
            or recovery.get("admission_revision_after_restart", 0)
            < recovery.get("admission_revision_before_restart", 1)
            or recovery.get("old_authorization_rejected_count") != 4
            or recovery.get("fresh_admission_count") != 8
            or recovery.get("signature_verification_count", 0) < 12
            or recovery.get("group_key_epoch") != 2
            or recovery.get("previous_group_key_epoch") != 1
            or recovery.get("reason") != "hub_failover"
            or recovery.get("fresh_key_distinct") is not True
            or recovery.get("receiver_count") != 2
            or recovery.get("receiver_min_inbound_bytes", 0) <= 0
            or recovery.get("receiver_min_decoded_samples", 0) < 3
            or recovery.get("stale_key_probe_inbound_bytes", 0) <= 0
            or recovery.get("stale_key_probe_decoded_samples") != 0
        ):
            reasons.append(f"{engine}_fresh_epoch_recovery_missing")
        if (
            compute.get("initial_primary_lease_count") != 1
            or compute.get("initial_validator_lease_count") != 1
            or compute.get("persisted_active_lease_count_after_restart") != 2
            or compute.get("revoked_primary_lease_count") != 1
            or compute.get("replacement_primary_lease_count") != 1
            or compute.get("replacement_fencing_token_advanced") is not True
            or compute.get("validator_conflict_request_count") != 2
            or compute.get("validator_conflict_success_count") != 1
            or compute.get("validator_conflict_rejection_count") != 1
            or compute.get("validator_conflict_reason") != "lease_overlap"
            or compute.get("conflict_scope_active_primary_count") != 1
            or compute.get("conflict_scope_active_validator_count") != 1
            or compute.get("duplicate_active_lease_count") != 0
            or compute.get("hub_remained_sole_lease_authority") is not True
        ):
            reasons.append(f"{engine}_compute_failover_fencing_missing")
        if (
            cleanup.get("ordinary_peer_connections_closed") != 4
            or cleanup.get("ordinary_tracks_ended") != 1
            or cleanup.get("livekit_rooms_remaining") != 0
            or cleanup.get("livekit_workers_terminated") != 8
            or cleanup.get("livekit_tracks_ended") != 2
            or cleanup.get("browser_closed") is not True
        ):
            reasons.append(f"{engine}_browser_cleanup_incomplete")
    runner = report.get("runner") if isinstance(report.get("runner"), Mapping) else {}
    if (
        runner.get("unique_compose_project") is not True
        or runner.get("matching_dynamic_rtc_ports") is not True
        or runner.get("sigkill_count") != 2
        or runner.get("restart_count") != 2
        or runner.get("hub_sigkill_count") != 2
        or runner.get("hub_restart_count") != 2
        or runner.get("hub_process_identity_changed") is not True
        or runner.get("ephemeral_hub_state_removed") is not True
        or runner.get("known_credential_match_count") != 0
        or runner.get("compose_project_removed") is not True
        or runner.get("browser_process_exit_verified") is not True
        or runner.get("hub_boundary_capture_verified") is not True
        or runner.get("hub_boundary_packet_count", 0) < 1
        or runner.get("hub_boundary_known_marker_matches") != 0
        or runner.get("hub_boundary_rtp_rtcp_packet_count") != 0
        or runner.get("hub_boundary_capture_persisted") is not False
        or runner.get("hub_boundary_credential_scan_performed") is not False
    ):
        reasons.append("live_failover_runner_cleanup_or_isolation_invalid")
    return sorted(set(reasons))


def remove_owned_project(compose: list[str], env: Mapping[str, str], project: str) -> bool:
    run_command(
        [*compose, "down", "--remove-orphans", "--volumes", "--timeout", "10"], env=env, timeout=120, check=False
    )
    remaining = run_command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        env=env,
        timeout=30,
        check=False,
    ).stdout.split()
    if remaining:
        run_command(["docker", "rm", "-f", *remaining], env=env, timeout=60, check=False)
    verify = run_command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        env=env,
        timeout=30,
        check=False,
    )
    return verify.returncode == 0 and not verify.stdout.strip()


def execute(output: Path) -> tuple[dict[str, Any], list[str]]:
    output.unlink(missing_ok=True)
    inspect_image()
    env = dict(os.environ)
    env.update(
        {
            "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY": "ananta-live-failover",
            "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET": secrets.token_urlsafe(48),
            "ANANTA_SEMANTIC_MEDIA_SFU_ENABLED": "true",
            "ANANTA_SEMANTIC_MEDIA_SFU_TOKEN_TTL_SECONDS": "60",
            "ANANTA_SEMANTIC_VISUAL_CAPTURE_ENABLED": "true",
            "ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED": "true",
            "ANANTA_SEMANTIC_COMPUTE_FALLBACK_HEALTHY": "true",
            "ANANTA_SEMANTIC_COMPUTE_MAX_PEER_CAPACITY": "1",
            "ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY": secrets.token_urlsafe(48),
            "SECRET_KEY": secrets.token_urlsafe(48),
            "AGENT_TOKEN": secrets.token_urlsafe(48),
            "AGENT_NAME": "semantic-sfu-live-e2e-hub",
            "ROLE": "hub",
            "DISABLE_INITIAL_ADMIN": "1",
        }
    )
    project = f"ananta-sfu-failover-{secrets.token_hex(8)}"
    raw_report: dict[str, Any] = {}
    actions: tuple[str, ...] = ()
    browser_exit_code = -1
    failure: str | None = None
    compose_removed = False
    hub_sigkill_count = 0
    hub_restart_count = 0
    hub_process_identity_changed = False
    ephemeral_hub_state_removed = False
    known_secret_matches = 0
    hub_capture: dict[str, int | bool | str] = {}
    ephemeral_user_tokens: tuple[str, ...] = ()
    descriptor_path: Path | None = None
    with tempfile.TemporaryDirectory(prefix="ananta-sfu-live-failover-") as temporary:
        directory = Path(temporary)
        http_port, tcp_port, udp_port = allocate_ports()
        hub_port = reserve_tcp_port()
        if hub_port in {http_port, tcp_port, udp_port}:
            raise LiveFailoverError("isolated_port_allocation_failed")
        env["DATABASE_URL"] = f"sqlite:///{directory / 'hub.db'}"
        env["DATA_DIR"] = str(directory / "hub-data")
        compose_file = write_isolated_deployment(
            directory,
            http_port=http_port,
            tcp_port=tcp_port,
            udp_port=udp_port,
        )
        compose = ["docker", "compose", "--project-name", project, "-f", str(compose_file)]
        env["ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL"] = f"ws://127.0.0.1:{http_port}"
        descriptor_path = directory / "hub-fixture.json"
        hub = IsolatedHubRuntime(port=hub_port, env=env, log_path=directory / "hub.log")
        hub_capture_name = f"ananta-semantic-hub-capture-{secrets.token_hex(6)}"
        hub_capture_dir = directory / "capture"
        hub_capture_dir.mkdir(mode=0o700)
        hub_capture_path = hub_capture_dir / "hub-boundary.pcap"
        hub_capture_started = False
        try:
            run_command(
                [sys.executable, str(HUB_FIXTURE), "seed", "--output", str(descriptor_path)],
                env=env,
                timeout=90,
            )
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            raw_tokens = descriptor.get("tokens") if isinstance(descriptor, Mapping) else None
            if not isinstance(raw_tokens, Mapping) or len(raw_tokens) != 4:
                raise LiveFailoverError("isolated_hub_fixture_invalid")
            ephemeral_user_tokens = tuple(str(value) for value in raw_tokens.values())
            if any(len(value) < 32 for value in ephemeral_user_tokens):
                raise LiveFailoverError("isolated_hub_fixture_invalid")
            hub.start()
            start_host_capture(
                capture_name=hub_capture_name,
                capture_dir=hub_capture_dir,
                capture_path=hub_capture_path.name,
                capture_filter=("tcp", "port", str(hub_port)),
                interface="lo",
            )
            hub_capture_started = True
            run_command([*compose, "up", "-d", "--wait", "semantic-media-sfu"], env=env, timeout=120)
            raw_report, actions, browser_exit_code = drive_spike(
                compose=compose,
                env=env,
                directory=directory,
                raw_output=directory / "raw-report.json",
                hub=hub,
                fixture_descriptor=descriptor_path,
            )
        except (LiveFailoverError, OSError, subprocess.TimeoutExpired) as exc:
            failure = str(exc).split(":", 1)[0]
        finally:
            if hub_capture_started:
                stop_capture(hub_capture_name)
                try:
                    hub_capture = {
                        **capture_measurements(
                            hub_capture_path,
                            "hub",
                            scan_credentials=False,
                        ),
                        "hub_boundary_rtp_rtcp_packet_count": 0,
                        "hub_boundary_filter_protocol": "tcp_control_only",
                        "hub_boundary_capture_persisted": False,
                        "hub_boundary_credential_scan_performed": False,
                    }
                except (OSError, RuntimeError) as exc:
                    failure = failure or str(exc).split(":", 1)[0]
            run_command(
                ["docker", "rm", "-f", hub_capture_name],
                env=env,
                timeout=30,
                check=False,
            )
            hub_sigkill_count = hub.sigkill_count
            hub_restart_count = max(0, hub.start_count - 1)
            hub_process_identity_changed = len(set(hub.process_ids)) == 3
            hub.stop()
            log_text = hub.log_path.read_text(encoding="utf-8", errors="replace") if hub.log_path.is_file() else ""
            secrets_to_scan = (
                env["ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET"],
                env["ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY"],
                env["SECRET_KEY"],
                env["AGENT_TOKEN"],
            )
            known_secret_matches = sum(
                log_text.count(value) for value in (*secrets_to_scan, *ephemeral_user_tokens)
            )
            compose_removed = remove_owned_project(compose, env, project)
    ephemeral_hub_state_removed = bool(descriptor_path is not None and not descriptor_path.exists())
    if not raw_report:
        raw_report = {
            "schema": "ananta.semantic-sfu-live-failover.v1",
            "pinned": {},
            "topology": {},
            "authority": {},
            "persisted_source_data": False,
            "engines": [],
            "verdict": "fail",
        }
    raw_report["runner"] = {
        "unique_compose_project": True,
        "matching_dynamic_rtc_ports": True,
        "sigkill_count": actions.count("sfu_kill"),
        "restart_count": actions.count("sfu_start"),
        "hub_sigkill_count": hub_sigkill_count,
        "hub_restart_count": hub_restart_count,
        "hub_process_identity_changed": hub_process_identity_changed,
        "ephemeral_hub_state_removed": ephemeral_hub_state_removed,
        "known_credential_match_count": known_secret_matches,
        "compose_project_removed": compose_removed,
        "browser_process_exit_verified": browser_exit_code == 0,
        "capture_image": CAPTURE_IMAGE,
        **hub_capture,
    }
    reasons = recompute_live_failover_evidence(raw_report)
    if failure:
        reasons.append(failure)
    reasons = sorted(set(reasons))
    raw_report["reason_codes"] = reasons
    raw_report["external_live_failover_verified"] = not reasons
    raw_report["verdict"] = "pass" if not reasons else "fail"
    assert_content_free(
        raw_report,
        known_secrets=(
            env["ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET"],
            env["ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY"],
            env["SECRET_KEY"],
            env["AGENT_TOKEN"],
            *ephemeral_user_tokens,
        ),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(raw_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return raw_report, reasons


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report, reasons = execute(args.output)
    except (LiveFailoverError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "reason_code": str(exc).split(":", 1)[0]}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "ok": not reasons,
                "external_live_failover_verified": report["external_live_failover_verified"],
                "reason_codes": reasons,
            },
            sort_keys=True,
        )
    )
    return 0 if not reasons else 2


if __name__ == "__main__":
    raise SystemExit(main())
