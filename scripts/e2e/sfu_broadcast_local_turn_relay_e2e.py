#!/usr/bin/env python3
"""Run a local real-browser LiveKit fan-out through a real coturn relay.

This runner intentionally produces local diagnostic output only. It cannot
mint release evidence, SRC_* identifiers, RUN_* identifiers, operator
approval, or production-capacity claims.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import ipaddress
import re
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.semantic-media.yml"
SPIKE = ROOT / "scripts/spikes/semantic_sfu_three_peer.mjs"
DEFAULT_OUTPUT = Path("/tmp/ananta-sfu-broadcast-local-turn-relay.json")
LIVEKIT_REPO_DIGEST = (
    "livekit/livekit-server@"
    "sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
)
TURN_REPO_DIGEST = (
    "coturn/coturn@"
    "sha256:71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888"
)
EXPECTED_ENGINES = ("chromium", "firefox")
OWNED_TURN_CONTAINER_PATTERN = re.compile(
    r"ananta-sfu-relay-[a-f0-9]{12}-turn-host"
)
TURN_PORT_LOCK = Path("/tmp/ananta-sfu-broadcast-turn-ports.lock")


class LocalTurnRelayError(RuntimeError):
    """Fail-closed local relay runner error."""


def run_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        executable = Path(command[0]).name
        reason = (
            "local_turn_relay_media_flow_failed"
            if executable == "node"
            else "local_turn_relay_container_command_failed"
        )
        raise LocalTurnRelayError(reason)
    return result


def reserve_port(*, socket_type: int, host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket_type) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def reserve_udp_port_range(
    host: str,
    *,
    size: int = 64,
) -> tuple[int, int]:
    for _attempt in range(64):
        start = 40_000 + secrets.randbelow(20_000 - size)
        listeners: list[socket.socket] = []
        try:
            for port in range(start, start + size):
                listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                listener.bind((host, port))
                listeners.append(listener)
        except OSError:
            continue
        finally:
            for listener in listeners:
                listener.close()
        if len(listeners) == size:
            return start, start + size - 1
    raise LocalTurnRelayError("local_turn_relay_udp_range_unavailable")


@contextmanager
def locked_turn_udp_ports(
    host: str,
    *,
    range_size: int = 64,
) -> Iterator[tuple[int, int, int]]:
    descriptor = os.open(
        TURN_PORT_LOCK,
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        listening_port = reserve_port(
            socket_type=socket.SOCK_DGRAM,
            host=host,
        )
        relay_min_port, relay_max_port = reserve_udp_port_range(
            host,
            size=range_size,
        )
        while relay_min_port <= listening_port <= relay_max_port:
            listening_port = reserve_port(
                socket_type=socket.SOCK_DGRAM,
                host=host,
            )
        yield listening_port, relay_min_port, relay_max_port
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def local_non_loopback_ipv4() -> str:
    candidates: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            candidates.add(str(probe.getsockname()[0]))
    except OSError:
        pass
    try:
        candidates.update(
            address[4][0]
            for address in socket.getaddrinfo(
                socket.gethostname(),
                None,
                family=socket.AF_INET,
                type=socket.SOCK_DGRAM,
            )
        )
    except OSError:
        pass
    for candidate in sorted(candidates):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address) and not (
            address.is_loopback
            or address.is_unspecified
            or address.is_multicast
            or address.is_link_local
        ):
            return str(address)
    raise LocalTurnRelayError("local_turn_relay_non_loopback_address_unavailable")


def build_host_turn_command(
    *,
    container_name: str,
    host: str,
    listening_port: int,
    relay_min_port: int,
    relay_max_port: int,
    username: str,
    password: str,
) -> list[str]:
    if OWNED_TURN_CONTAINER_PATTERN.fullmatch(container_name) is None:
        raise LocalTurnRelayError("local_turn_relay_container_name_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise LocalTurnRelayError("local_turn_relay_host_address_invalid") from exc
    if not isinstance(address, ipaddress.IPv4Address) or address.is_loopback:
        raise LocalTurnRelayError("local_turn_relay_host_address_invalid")
    if (
        not 1024 <= listening_port <= 65_535
        or not 1024 <= relay_min_port <= relay_max_port <= 65_535
        or relay_max_port - relay_min_port + 1 < 32
    ):
        raise LocalTurnRelayError("local_turn_relay_port_range_invalid")
    if not username or len(password) < 32:
        raise LocalTurnRelayError("local_turn_relay_credential_invalid")
    return [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--label",
        f"ananta.e2e.owner={container_name}",
        "--network",
        "host",
        "--init",
        "--user",
        "65534:65534",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "NET_BIND_SERVICE",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "64",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--tmpfs",
        "/var/lib/coturn:rw,noexec,nosuid,nodev,size=4m,uid=65534,gid=65534",
        TURN_REPO_DIGEST,
        "--no-cli",
        "--no-tls",
        "--no-dtls",
        "--fingerprint",
        "--lt-cred-mech",
        "--realm=ananta.sfu-broadcast.local-relay",
        f"--user={username}:{password}",
        f"--listening-ip={host}",
        f"--relay-ip={host}",
        f"--external-ip={host}",
        f"--listening-port={listening_port}",
        f"--min-port={relay_min_port}",
        f"--max-port={relay_max_port}",
        "--no-multicast-peers",
        "--log-file=stdout",
        "--pidfile=",
    ]


def wait_for_running_container(
    container_name: str,
    *,
    env: Mapping[str, str],
) -> None:
    if OWNED_TURN_CONTAINER_PATTERN.fullmatch(container_name) is None:
        raise LocalTurnRelayError("local_turn_relay_container_name_invalid")
    for _attempt in range(50):
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}",
                container_name,
            ],
            cwd=ROOT,
            env=dict(env),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(0.1)
    raise LocalTurnRelayError("local_turn_relay_container_not_running")


def remove_owned_turn_container(
    container_name: str,
    *,
    env: Mapping[str, str],
) -> bool:
    if OWNED_TURN_CONTAINER_PATTERN.fullmatch(container_name) is None:
        return False
    subprocess.run(
        ["docker", "rm", "--force", container_name],
        cwd=ROOT,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    inspection = subprocess.run(
        ["docker", "inspect", container_name],
        cwd=ROOT,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return inspection.returncode != 0


def service_container_id(
    compose: Sequence[str],
    service: str,
    *,
    env: Mapping[str, str],
) -> str:
    identifier = run_command(
        [*compose, "ps", "-q", service],
        env=env,
        timeout=30,
    ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", identifier):
        raise LocalTurnRelayError(f"local_turn_relay_container_missing:{service}")
    return identifier


def verify_container_image(
    container_id: str,
    expected_repo_digest: str,
    *,
    env: Mapping[str, str],
) -> str:
    image_id = run_command(
        ["docker", "inspect", "--format", "{{.Image}}", container_id],
        env=env,
        timeout=30,
    ).stdout.strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise LocalTurnRelayError("local_turn_relay_image_id_invalid")
    raw_repo_digests = run_command(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id],
        env=env,
        timeout=30,
    ).stdout.strip()
    try:
        repo_digests = json.loads(raw_repo_digests)
    except json.JSONDecodeError as exc:
        raise LocalTurnRelayError("local_turn_relay_image_digest_invalid") from exc
    if not isinstance(repo_digests, list) or expected_repo_digest not in repo_digests:
        raise LocalTurnRelayError("local_turn_relay_image_digest_mismatch")
    return image_id


def validate_relay_report(
    report: Mapping[str, Any],
    *,
    expected_engines: Sequence[str] = EXPECTED_ENGINES,
    receiver_count: int = 3,
) -> tuple[str, ...]:
    """Recompute the local verdict without trusting the spike verdict field."""

    reasons: set[str] = set()
    if report.get("schema") != "ananta.semantic-sfu-three-peer-spike.v1":
        reasons.add("local_turn_relay_schema_invalid")
    if report.get("release_evidence") is not False:
        reasons.add("local_turn_relay_release_boundary_invalid")
    if report.get("transport_profile") != "turn_relay_required":
        reasons.add("local_turn_relay_transport_profile_invalid")
    topology = report.get("topology")
    if (
        not isinstance(topology, Mapping)
        or topology.get("publishers") != 1
        or topology.get("receivers") != receiver_count
        or topology.get("expected_publisher_publications") != 1
    ):
        reasons.add("local_turn_relay_topology_invalid")
    e2ee = report.get("e2ee")
    if (
        not isinstance(e2ee, Mapping)
        or e2ee.get("enabled") is not True
        or e2ee.get("server_plaintext_access") is not False
    ):
        reasons.add("local_turn_relay_e2ee_invalid")

    rows = report.get("engines")
    if not isinstance(rows, list):
        rows = []
        reasons.add("local_turn_relay_engine_inventory_invalid")
    by_engine = {
        str(row.get("engine")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("engine"), str)
    }
    if set(by_engine) != set(expected_engines) or len(rows) != len(expected_engines):
        reasons.add("local_turn_relay_engine_inventory_invalid")

    expected_receivers = {f"receiver-{index}" for index in range(1, receiver_count + 1)}
    expected_identities = {"publisher", "wrong-key-probe", *expected_receivers}
    for engine in expected_engines:
        row = by_engine.get(engine)
        if not isinstance(row, Mapping):
            continue
        if row.get("relay_required") is not True or row.get("relay_selected") is not True:
            reasons.add(f"local_turn_relay_{engine}_selection_invalid")
        peers = row.get("peers")
        if not isinstance(peers, list):
            reasons.add(f"local_turn_relay_{engine}_peer_inventory_invalid")
            continue
        by_identity = {
            str(peer.get("identity")): peer
            for peer in peers
            if isinstance(peer, Mapping) and isinstance(peer.get("identity"), str)
        }
        if set(by_identity) != expected_identities or len(peers) != len(expected_identities):
            reasons.add(f"local_turn_relay_{engine}_peer_inventory_invalid")
            continue
        for identity, peer in by_identity.items():
            candidate_types = peer.get("selected_candidate_types")
            if (
                not isinstance(candidate_types, list)
                or not candidate_types
                or not all(
                    isinstance(candidate, str) and candidate.startswith("relay:")
                    for candidate in candidate_types
                )
            ):
                reasons.add(f"local_turn_relay_{engine}_candidate_invalid")
                break
            if not isinstance(peer.get("peer_connections"), int) or peer["peer_connections"] < 1:
                reasons.add(f"local_turn_relay_{engine}_peer_connection_invalid")
        publisher = by_identity["publisher"]
        if (
            publisher.get("peer_connections") != 1
            or publisher.get("local_video_publication_count") != 1
            or not isinstance(publisher.get("outbound_video_streams"), int)
            or publisher["outbound_video_streams"] < 1
            or not isinstance(publisher.get("outbound_video_bytes"), int)
            or publisher["outbound_video_bytes"] <= 0
        ):
            reasons.add(f"local_turn_relay_{engine}_publisher_flow_invalid")
        for identity in expected_receivers:
            receiver = by_identity[identity]
            if (
                receiver.get("inbound_video_streams") != 1
                or not isinstance(receiver.get("inbound_video_bytes"), int)
                or receiver["inbound_video_bytes"] <= 0
                or not isinstance(receiver.get("decoded_samples"), int)
                or receiver["decoded_samples"] < 3
            ):
                reasons.add(f"local_turn_relay_{engine}_receiver_flow_invalid")
                break
        wrong_key = by_identity["wrong-key-probe"]
        if (
            wrong_key.get("inbound_video_streams") != 1
            or not isinstance(wrong_key.get("inbound_video_bytes"), int)
            or wrong_key["inbound_video_bytes"] <= 0
            or wrong_key.get("decoded_samples") != 0
        ):
            reasons.add(f"local_turn_relay_{engine}_wrong_key_probe_invalid")
    return tuple(sorted(reasons))


def compose_project_removed(
    compose: Sequence[str],
    *,
    env: Mapping[str, str],
) -> bool:
    result = subprocess.run(
        [*compose, "ps", "-aq"],
        cwd=ROOT,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0 and not result.stdout.strip()


def atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def execute(output: Path, *, receiver_count: int) -> Mapping[str, Any]:
    http_port = reserve_port(socket_type=socket.SOCK_STREAM)
    rtc_tcp_port = reserve_port(socket_type=socket.SOCK_STREAM)
    rtc_udp_port = reserve_port(socket_type=socket.SOCK_DGRAM)
    turn_host = local_non_loopback_ipv4()
    project = f"ananta-sfu-relay-{secrets.token_hex(6)}"
    turn_container_name = f"{project}-turn-host"
    livekit_key = f"local-{secrets.token_hex(8)}"
    livekit_secret = secrets.token_urlsafe(48)
    turn_user = f"local-{secrets.token_hex(8)}"
    turn_password = secrets.token_urlsafe(48)
    env = dict(os.environ)
    env.update(
        {
            "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY": livekit_key,
            "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET": livekit_secret,
            "ANANTA_SEMANTIC_MEDIA_SFU_HTTP_PORT": str(http_port),
            "ANANTA_SEMANTIC_MEDIA_SFU_TCP_PORT": str(rtc_tcp_port),
            "ANANTA_SEMANTIC_MEDIA_SFU_UDP_PORT": str(rtc_udp_port),
            "ANANTA_SEMANTIC_MEDIA_SFU_TURN_UDP_PORT": str(
                reserve_port(socket_type=socket.SOCK_DGRAM)
            ),
            "ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL": f"ws://127.0.0.1:{http_port}",
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_BIND_IP": turn_host,
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_EXTERNAL_IP": turn_host,
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_USER": turn_user,
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_PASSWORD": turn_password,
            "ANANTA_SEMANTIC_MEDIA_SFU_BROWSER_ENGINES": ",".join(EXPECTED_ENGINES),
            "ANANTA_SEMANTIC_MEDIA_SFU_RECEIVER_COUNT": str(receiver_count),
            "ANANTA_SEMANTIC_MEDIA_SFU_WRONG_KEY_PROBE": "1",
            "ANANTA_SEMANTIC_MEDIA_SFU_FORCE_RELAY": "1",
        }
    )
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(COMPOSE),
        "--profile",
        "semantic-media-sfu",
    ]
    report: Mapping[str, Any] | None = None
    image_ids: dict[str, str] = {}
    cleanup_error: str | None = None
    try:
        with locked_turn_udp_ports(turn_host) as (
            turn_udp_port,
            relay_min_port,
            relay_max_port,
        ):
            env.update(
                {
                    "ANANTA_SEMANTIC_MEDIA_TURN_GATE_PORT": str(turn_udp_port),
                    "ANANTA_SEMANTIC_MEDIA_TURN_GATE_URL": (
                        f"turn:{turn_host}:{turn_udp_port}?transport=udp"
                    ),
                }
            )
            turn_start = run_command(
                build_host_turn_command(
                    container_name=turn_container_name,
                    host=turn_host,
                    listening_port=turn_udp_port,
                    relay_min_port=relay_min_port,
                    relay_max_port=relay_max_port,
                    username=turn_user,
                    password=turn_password,
                ),
                env=env,
                timeout=60,
            )
            if not re.fullmatch(r"[a-f0-9]{64}", turn_start.stdout.strip()):
                raise LocalTurnRelayError("local_turn_relay_container_id_invalid")
            wait_for_running_container(turn_container_name, env=env)
        run_command(
            [
                *compose,
                "up",
                "-d",
                "--wait",
                "semantic-media-sfu",
            ],
            env=env,
            timeout=180,
        )
        livekit_container = service_container_id(
            compose, "semantic-media-sfu", env=env
        )
        image_ids = {
            "livekit": verify_container_image(
                livekit_container, LIVEKIT_REPO_DIGEST, env=env
            ),
            "coturn": verify_container_image(
                turn_container_name, TURN_REPO_DIGEST, env=env
            ),
        }
        with tempfile.TemporaryDirectory(prefix="ananta-sfu-turn-relay-") as temporary:
            spike_output = Path(temporary) / "relay.json"
            child_env = dict(env)
            child_env["ANANTA_SEMANTIC_MEDIA_SFU_SPIKE_OUTPUT"] = str(spike_output)
            run_command(
                ["node", str(SPIKE)],
                env=child_env,
                timeout=300,
            )
            try:
                loaded = json.loads(spike_output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LocalTurnRelayError(
                    "local_turn_relay_report_unavailable"
                ) from exc
            if not isinstance(loaded, Mapping):
                raise LocalTurnRelayError("local_turn_relay_report_invalid")
            report = loaded
            reasons = validate_relay_report(
                report,
                receiver_count=receiver_count,
            )
            if reasons:
                raise LocalTurnRelayError(reasons[0])
    finally:
        cleanup = subprocess.run(
            [*compose, "down", "--remove-orphans"],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if cleanup.returncode != 0 or not compose_project_removed(compose, env=env):
            cleanup_error = "local_turn_relay_compose_cleanup_failed"
        if not remove_owned_turn_container(turn_container_name, env=env):
            cleanup_error = "local_turn_relay_turn_cleanup_failed"
    if cleanup_error:
        raise LocalTurnRelayError(cleanup_error)
    if report is None:
        raise LocalTurnRelayError("local_turn_relay_report_unavailable")

    result = {
        "schema": "ananta.sfu-broadcast-local-turn-relay-diagnostic.v1",
        "status": "passed",
        "scope": "local_diagnostic_not_release_evidence",
        "release_evidence": False,
        "claims": {
            "real_browser_contexts": True,
            "real_livekit_process": True,
            "real_coturn_relay_selected": True,
            "wrong_key_media_not_decoded": True,
            "production_capacity": False,
        },
        "pinned_images": {
            "livekit": LIVEKIT_REPO_DIGEST,
            "coturn": TURN_REPO_DIGEST,
        },
        "container_image_ids": image_ids,
        "cleanup": {
            "compose_project_removed": True,
            "host_turn_container_removed": True,
        },
        "source_report": report,
    }
    atomic_write(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local real-browser SFU media through a real coturn relay."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receiver-count", type=int, default=3)
    args = parser.parse_args()
    if not 3 <= args.receiver_count <= 7:
        parser.error("--receiver-count must be between 3 and 7")
    try:
        result = execute(args.output, receiver_count=args.receiver_count)
    except (
        LocalTurnRelayError,
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        reason = str(exc).split(":", 1)[0]
        print(json.dumps({"ok": False, "reason_code": reason}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "scope": result["scope"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
