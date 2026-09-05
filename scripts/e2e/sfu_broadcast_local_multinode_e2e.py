#!/usr/bin/env python3
"""Exercise two real LiveKit nodes with TLS Redis and real browsers locally.

The harness owns every temporary credential, container and volume it creates.
It emits bounded, content-free observations and deliberately makes no public
network, independent failure-domain or production-capacity claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import (  # noqa: E402
    TURN_REPO_DIGEST,
    build_host_turn_command,
    local_non_loopback_ipv4,
    locked_turn_udp_ports,
    remove_owned_turn_container,
    wait_for_running_container,
)

COMPOSE_FILE = ROOT / "docker-compose.sfu-broadcast.yml"
MEDIA_SPIKE = ROOT / "scripts/spikes/semantic_sfu_three_peer.mjs"
LIVEKIT_IMAGE = "livekit/livekit-server@sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
REDIS_IMAGE = "redis:7.4.2-alpine@sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952"
SERVICE_A = "sfu-broadcast-livekit-native-a"
SERVICE_B = "sfu-broadcast-livekit-native-b"
REDIS_SERVICE = "sfu-broadcast-redis"


class LocalMultinodeError(RuntimeError):
    """Bounded local-harness failure."""


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    timeout: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        executable = Path(command[0]).name
        reason = (
            "local_multinode_media_flow_failed"
            if executable == "node"
            else f"local_multinode_command_failed:{executable}"
        )
        raise LocalMultinodeError(reason)
    return completed


def _write(path: Path, value: str, *, mode: int = 0o600) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def _generate_tls(directory: Path, *, environment: Mapping[str, str]) -> None:
    ca_key = directory / "ca.key"
    ca_cert = directory / "ca.crt"
    server_key = directory / "server.key"
    server_csr = directory / "server.csr"
    server_cert = directory / "server.crt"
    client_key = directory / "client.key"
    client_csr = directory / "client.csr"
    client_cert = directory / "client.crt"
    extension = directory / "server.ext"
    _write(extension, "subjectAltName=DNS:sfu-broadcast-redis\nextendedKeyUsage=serverAuth\n")
    commands = (
        (
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=ananta-local-multinode-ca",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_cert),
        ),
        (
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=sfu-broadcast-redis",
            "-keyout",
            str(server_key),
            "-out",
            str(server_csr),
        ),
        (
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(server_csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-extfile",
            str(extension),
            "-out",
            str(server_cert),
        ),
        (
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=livekit",
            "-keyout",
            str(client_key),
            "-out",
            str(client_csr),
        ),
        (
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(client_csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(client_cert),
        ),
    )
    for command in commands:
        _run(command, environment=environment, timeout=30)
    for identity in ("999", "65532"):
        _run(("setfacl", "-m", f"u:{identity}:rX", str(directory)), environment=environment, timeout=10)
        for path in (ca_cert, server_key, server_cert, client_key, client_cert):
            _run(("setfacl", "-m", f"u:{identity}:r", str(path)), environment=environment, timeout=10)


def _runtime_material(
    directory: Path,
    *,
    redis_password: str,
    redis_port: int,
    api_ports: Mapping[str, int],
    udp_ports: Mapping[str, int],
    node_ip: str,
) -> tuple[Path, dict[str, Path]]:
    acl_path = directory / "users.acl"
    _write(
        acl_path,
        "user default off\n"
        f"user livekit on >{redis_password} ~* &* +@all\n"
        f"user health on >{secrets.token_urlsafe(36)} ~* &* +ping\n",
    )
    for identity in ("999", "65532"):
        subprocess.run(("setfacl", "-m", f"u:{identity}:r", str(acl_path)), check=True)
    redis_config = directory / "redis.conf"
    _write(
        redis_config,
        "port 0\n"
        "bind 127.0.0.1\n"
        f"tls-port {redis_port}\n"
        "tls-cert-file /run/sfu-redis-tls/server.crt\n"
        "tls-key-file /run/sfu-redis-tls/server.key\n"
        "tls-ca-cert-file /run/sfu-redis-tls/ca.crt\n"
        "tls-auth-clients yes\n"
        "aclfile /run/sfu-redis-tls/users.acl\n"
        "databases 8\n"
        "appendonly no\n"
        "protected-mode yes\n"
        'rename-command FLUSHALL ""\n'
        'rename-command FLUSHDB ""\n'
        'rename-command CONFIG ""\n',
    )
    subprocess.run(("setfacl", "-m", "u:999:r", str(redis_config)), check=True)
    livekit_configs: dict[str, Path] = {}
    for service in (SERVICE_A, SERVICE_B):
        config_path = directory / f"{service}.yaml"
        _write(
            config_path,
            f"port: {api_ports[service]}\n"
            "bind_addresses: [0.0.0.0]\n"
            "rtc:\n"
            "  tcp_port: 0\n"
            f"  udp_port: {udp_ports[service]}\n"
            "  use_external_ip: false\n"
            f"  node_ip: {node_ip}\n"
            "redis:\n"
            f"  address: 127.0.0.1:{redis_port}\n"
            "  username: livekit\n"
            f"  password: {redis_password}\n"
            "  db: 7\n"
            "  tls:\n"
            "    enabled: true\n"
            "    insecure: false\n"
            "    server_name: sfu-broadcast-redis\n"
            "    ca_cert_file: /run/sfu-redis-tls/ca.crt\n"
            "    client_cert_file: /run/sfu-redis-tls/client.crt\n"
            "    client_key_file: /run/sfu-redis-tls/client.key\n"
            "logging:\n"
            "  level: info\n"
            "room:\n"
            "  empty_timeout: 60\n"
            "  departure_timeout: 20\n"
            "  max_participants: 250\n",
        )
        subprocess.run(("setfacl", "-m", "u:65532:r", str(config_path)), check=True)
        livekit_configs[service] = config_path
    return redis_config, livekit_configs


def _reserve_port(socket_type: int, *, host: str) -> int:
    with socket.socket(socket.AF_INET, socket_type) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def _image_id(container_id: str, *, environment: Mapping[str, str]) -> str:
    value = _run(
        ("docker", "inspect", "--format", "{{.Image}}", container_id),
        environment=environment,
        timeout=15,
    ).stdout.strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise LocalMultinodeError("local_multinode_image_id_invalid")
    return value


def _node_count(container_id: str, *, environment: Mapping[str, str]) -> int:
    completed = _run(
        ("docker", "exec", container_id, "/livekit-server", "--config", "/etc/livekit/livekit.yaml", "list-nodes"),
        environment=environment,
        timeout=30,
    )
    matches = sorted(set(re.findall(r"\bND_[A-Za-z0-9]+\b", completed.stdout)))
    if not matches:
        raise LocalMultinodeError("local_multinode_node_listing_invalid")
    return len(matches)


def _wait_for_node_count(
    container_id: str,
    expected: int,
    *,
    environment: Mapping[str, str],
    timeout_seconds: int = 30,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    observed = 0
    last_error = ""
    while time.monotonic() < deadline:
        try:
            observed = _node_count(container_id, environment=environment)
        except LocalMultinodeError as exc:
            observed = 0
            last_error = str(exc)
        if observed == expected:
            return observed
        time.sleep(0.5)
    raise LocalMultinodeError(f"local_multinode_node_count_timeout:{observed}:{expected}:{last_error}")


def _run_media(
    *,
    endpoint: str,
    api_key: str,
    api_secret: str,
    output: Path,
    environment: Mapping[str, str],
    turn_url: str,
    turn_username: str,
    turn_password: str,
) -> dict[str, Any]:
    media_environment = dict(environment)
    media_environment.update(
        {
            "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY": api_key,
            "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET": api_secret,
            "ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL": f"ws://{endpoint}",
            "ANANTA_SEMANTIC_MEDIA_SFU_BROWSER_ENGINES": "chromium,firefox",
            "ANANTA_SEMANTIC_MEDIA_SFU_RECEIVER_COUNT": "2",
            "ANANTA_SEMANTIC_MEDIA_SFU_WRONG_KEY_PROBE": "1",
            "ANANTA_SEMANTIC_MEDIA_SFU_FORCE_RELAY": "1",
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_URL": turn_url,
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_USER": turn_username,
            "ANANTA_SEMANTIC_MEDIA_TURN_GATE_PASSWORD": turn_password,
            "ANANTA_SEMANTIC_MEDIA_SFU_SPIKE_OUTPUT": str(output),
        }
    )
    _run(("node", str(MEDIA_SPIKE)), environment=media_environment, timeout=120)
    try:
        result = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalMultinodeError("local_multinode_media_report_invalid") from exc
    if not isinstance(result, dict):
        raise LocalMultinodeError("local_multinode_media_report_invalid")
    return result


def project_media(result: Mapping[str, Any]) -> dict[str, Any]:
    engines = []
    for row in result.get("engines") or []:
        if not isinstance(row, Mapping):
            continue
        peers = [value for value in row.get("peers") or [] if isinstance(value, Mapping)]
        publisher = next((value for value in peers if value.get("identity") == "publisher"), {})
        receivers = [value for value in peers if str(value.get("identity") or "").startswith("receiver-")]
        wrong_key = next((value for value in peers if value.get("identity") == "wrong-key-probe"), {})
        engines.append(
            {
                "engine": row.get("engine"),
                "verdict": row.get("verdict"),
                "publisher_outbound_video_bytes": publisher.get("outbound_video_bytes"),
                "receiver_inbound_video_bytes": [value.get("inbound_video_bytes") for value in receivers],
                "receiver_decoded_samples": [value.get("decoded_samples") for value in receivers],
                "wrong_key_inbound_video_bytes": wrong_key.get("inbound_video_bytes"),
                "wrong_key_decoded_samples": wrong_key.get("decoded_samples"),
            }
        )
    return {"verdict": result.get("verdict"), "engines": engines}


def media_passed(projection: Mapping[str, Any]) -> bool:
    engines = list(projection.get("engines") or [])
    return bool(
        projection.get("verdict") == "pass"
        and {row.get("engine") for row in engines} == {"chromium", "firefox"}
        and all(
            row.get("verdict") == "pass"
            and int(row.get("publisher_outbound_video_bytes") or 0) > 0
            and len(row.get("receiver_inbound_video_bytes") or []) == 2
            and all(int(value or 0) > 0 for value in row.get("receiver_inbound_video_bytes") or [])
            and all(int(value or 0) >= 3 for value in row.get("receiver_decoded_samples") or [])
            and int(row.get("wrong_key_inbound_video_bytes") or 0) > 0
            and row.get("wrong_key_decoded_samples") == 0
            for row in engines
        )
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait_tcp(host: str, port: int, *, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise LocalMultinodeError("local_multinode_tcp_endpoint_unreachable")


def execute(output: Path) -> dict[str, Any]:
    project = f"ananta-sfu-multinode-{secrets.token_hex(6)}"
    base_environment = dict(os.environ)
    api_key = f"ananta-{secrets.token_hex(8)}"
    api_secret = secrets.token_urlsafe(48)
    redis_password = secrets.token_urlsafe(48)
    turn_host = local_non_loopback_ipv4()
    turn_container = f"ananta-sfu-relay-{secrets.token_hex(6)}-turn-host"
    turn_username = f"local-{secrets.token_hex(8)}"
    turn_password = secrets.token_urlsafe(48)
    container_names = {
        REDIS_SERVICE: f"{project}-redis",
        SERVICE_A: f"{project}-livekit-a",
        SERVICE_B: f"{project}-livekit-b",
    }
    redis_port = _reserve_port(socket.SOCK_STREAM, host="127.0.0.1")
    api_ports = {
        SERVICE_A: _reserve_port(socket.SOCK_STREAM, host="127.0.0.1"),
        SERVICE_B: _reserve_port(socket.SOCK_STREAM, host="127.0.0.1"),
    }
    udp_ports = {
        SERVICE_A: _reserve_port(socket.SOCK_DGRAM, host=turn_host),
        SERVICE_B: _reserve_port(socket.SOCK_DGRAM, host=turn_host),
    }
    started = time.monotonic()
    cleanup_complete = False
    turn_container_removed = False
    with (
        tempfile.TemporaryDirectory(prefix="ananta-sfu-multinode-material-") as temporary,
        locked_turn_udp_ports(turn_host) as turn_ports,
    ):
        material = Path(temporary)
        _generate_tls(material, environment=base_environment)
        redis_config, livekit_configs = _runtime_material(
            material,
            redis_password=redis_password,
            redis_port=redis_port,
            api_ports=api_ports,
            udp_ports=udp_ports,
            node_ip=turn_host,
        )
        environment = dict(base_environment)
        turn_port, turn_min_port, turn_max_port = turn_ports
        turn_url = f"turn:{turn_host}:{turn_port}?transport=udp"
        _run(
            build_host_turn_command(
                container_name=turn_container,
                host=turn_host,
                listening_port=turn_port,
                relay_min_port=turn_min_port,
                relay_max_port=turn_max_port,
                username=turn_username,
                password=turn_password,
            ),
            environment=environment,
            timeout=45,
        )
        wait_for_running_container(turn_container, env=environment)
        try:
            _run(
                (
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    container_names[REDIS_SERVICE],
                    "--network",
                    "host",
                    "--user",
                    "999:999",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--pids-limit",
                    "128",
                    "--memory",
                    "512m",
                    "--cpus",
                    "1",
                    "--tmpfs",
                    "/data:rw,noexec,nosuid,nodev,size=64m,uid=999,gid=999",
                    "--volume",
                    f"{redis_config}:/etc/redis/redis.conf:ro",
                    "--volume",
                    f"{material}:/run/sfu-redis-tls:ro",
                    REDIS_IMAGE,
                    "redis-server",
                    "/etc/redis/redis.conf",
                ),
                environment=environment,
                timeout=60,
            )
            _wait_tcp("127.0.0.1", redis_port)
            for service in (SERVICE_A, SERVICE_B):
                _run(
                    (
                        "docker",
                        "run",
                        "--detach",
                        "--name",
                        container_names[service],
                        "--network",
                        "host",
                        "--user",
                        "65532:65532",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges:true",
                        "--pids-limit",
                        "256",
                        "--memory",
                        "1g",
                        "--cpus",
                        "2",
                        "--tmpfs",
                        "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532",
                        "--volume",
                        f"{livekit_configs[service]}:/etc/livekit/livekit.yaml:ro",
                        "--volume",
                        f"{material}:/run/sfu-redis-tls:ro",
                        "--env",
                        f"LIVEKIT_KEYS={api_key}: {api_secret}",
                        LIVEKIT_IMAGE,
                        "--config",
                        "/etc/livekit/livekit.yaml",
                    ),
                    environment=environment,
                    timeout=60,
                )
                _wait_tcp("127.0.0.1", api_ports[service])
            initial_nodes = _wait_for_node_count(container_names[SERVICE_A], 2, environment=environment)
            _run(
                ("docker", "stop", "--time", "15", container_names[SERVICE_A]),
                environment=environment,
                timeout=45,
            )
            drain_started = time.monotonic()
            drained_nodes = _wait_for_node_count(container_names[SERVICE_B], 1, environment=environment)
            after_drain = project_media(
                _run_media(
                    endpoint=f"127.0.0.1:{api_ports[SERVICE_B]}",
                    api_key=api_key,
                    api_secret=api_secret,
                    output=material / "after-drain-media.json",
                    environment=environment,
                    turn_url=turn_url,
                    turn_username=turn_username,
                    turn_password=turn_password,
                )
            )
            drain_recovery_ms = round((time.monotonic() - drain_started) * 1000)
            _run(
                ("docker", "start", container_names[SERVICE_A]),
                environment=environment,
                timeout=45,
            )
            _wait_tcp("127.0.0.1", api_ports[SERVICE_A])
            rejoined_nodes = _wait_for_node_count(container_names[SERVICE_B], 2, environment=environment)
            image_ids = {
                service: _image_id(container_names[service], environment=environment)
                for service in (REDIS_SERVICE, SERVICE_A, SERVICE_B)
            }
            image_ids["coturn"] = _image_id(turn_container, environment=environment)
            passed = bool(
                initial_nodes == 2 and drained_nodes == 1 and rejoined_nodes == 2 and media_passed(after_drain)
            )
            report: dict[str, Any] = {
                "schema": "ananta.sfu-broadcast-local-multinode.v1",
                "status": "passed" if passed else "failed",
                "scope": "local_single_host",
                "claims": {
                    "real_livekit_processes": True,
                    "real_tls_redis_process": True,
                    "real_browser_processes": True,
                    "native_placement_owner": "livekit",
                    "public_network_path": False,
                    "independent_failure_domains": False,
                    "production_capacity": False,
                },
                "pinned_images": {
                    "livekit": LIVEKIT_IMAGE,
                    "redis": REDIS_IMAGE,
                    "coturn": TURN_REPO_DIGEST,
                },
                "container_image_ids": image_ids,
                "topology": {"livekit_nodes": 2, "redis_nodes": 1, "host_count": 1},
                "observations": {
                    "initial_registered_nodes": initial_nodes,
                    "drained_registered_nodes": drained_nodes,
                    "rejoined_registered_nodes": rejoined_nodes,
                    "drain_recovery_ms": drain_recovery_ms,
                    "after_drain_media": after_drain,
                },
                "bindings": {
                    "compose_sha256": sha256_file(COMPOSE_FILE),
                    "media_spike_sha256": sha256_file(MEDIA_SPIKE),
                },
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        finally:
            for container_name in container_names.values():
                _run(
                    ("docker", "rm", "--force", container_name),
                    environment=environment,
                    timeout=30,
                    check=False,
                )
            cleanup_complete = all(
                _run(
                    ("docker", "inspect", container_name),
                    environment=environment,
                    timeout=15,
                    check=False,
                ).returncode
                != 0
                for container_name in container_names.values()
            )
            turn_container_removed = remove_owned_turn_container(turn_container, env=environment)
    report["cleanup"] = {
        "owned_containers_and_volumes_removed": cleanup_complete,
        "owned_turn_container_removed": turn_container_removed,
    }
    if not cleanup_complete or not turn_container_removed:
        report["status"] = "failed"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/ananta-sfu-broadcast-local-multinode.json"))
    arguments = parser.parse_args()
    try:
        report = execute(arguments.output)
    except (LocalMultinodeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "reason_code": str(exc)[:200]}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(arguments.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
