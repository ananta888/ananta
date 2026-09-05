#!/usr/bin/env python3
"""Fail-closed capability probe for the pinned LiveKit broadcast boundary.

Only this process' own Docker/browser execution can create the private runtime
attestation accepted by the evaluator. JSON files and mocks are never accepted
as runtime evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/domain/livekit-broadcast-runtime-capabilities.json"
COMPOSE_RELATIVE = Path("docker-compose.semantic-media.yml")
CONFIG_RELATIVE = Path("config/livekit.semantic-media.yaml")
PACKAGE_RELATIVE = Path("frontend-angular/package.json")
LOCK_RELATIVE = Path("frontend-angular/package-lock.json")
ADAPTER_RELATIVE = Path("frontend-angular/src/app/services/livekit-sfu-room.adapter.ts")
SPIKE_RELATIVE = Path("scripts/spikes/semantic_sfu_three_peer.mjs")

EXPECTED_SERVER_VERSION = "1.13.1"
EXPECTED_CLIENT_VERSION = "2.20.1"
EXPECTED_IMAGE_DIGEST = "sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
EXPECTED_IMAGE_REFERENCE = f"livekit/livekit-server@{EXPECTED_IMAGE_DIGEST}"
EXPECTED_ROOM_PARTICIPANT_CEILING = 250
RUNTIME_CONTROL_MODE = "livekit_control_api"
PLACEMENT_OWNER = "livekit_native"
ROOM_NAME = "ananta-spike-chromium"
_RUNTIME_ORIGIN = object()


class ProbeFailure(RuntimeError):
    """Closed, public reason code for an expected probe failure."""


@dataclass(frozen=True, slots=True)
class StaticBindings:
    image_reference: str | None
    client_package_version: str | None
    client_lock_version: str | None
    client_lock_integrity: str | None
    config_sha256: str
    source_sha256: str
    source_digests: Mapping[str, str]
    config_text: str = field(repr=False)
    adapter_text: str = field(repr=False)
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    provenance: str
    image_reference: str | None
    image_id_sha256: str | None
    server_version: str | None
    config_sha256: str | None
    container_id_sha256: str | None
    smoke_report_sha256: str | None
    publisher_count: int
    receiver_count: int
    publisher_upload_count: int
    publisher_publication_count: int
    decoded_receiver_count: int
    room_service_update_subscriptions: bool
    room_service_send_data: bool
    participant_permissions: bool
    e2ee: bool
    cleanup_complete: bool
    reason_codes: tuple[str, ...]
    _origin: object = field(repr=False, compare=False, default=None)

    @property
    def trusted(self) -> bool:
        return (
            self._origin is _RUNTIME_ORIGIN
            and self.provenance == "live_container"
            and self.cleanup_complete
            and not self.reason_codes
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return _sha256_bytes(encoded)


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ProbeFailure("static_json_shape_invalid")
    return value


def _client_stream_policy_bound(adapter_text: str) -> bool:
    """Recognize the supported adaptive/manual policy without executing TS."""

    adaptive_stream = re.search(
        r"adaptiveStream\s*:\s*options\.layerControlMode\s*===\s*['\"]adaptive_stream['\"]",
        adapter_text,
    )
    dynacast = re.search(r"dynacast\s*:\s*true\b", adapter_text)
    return adaptive_stream is not None and dynacast is not None


def collect_static_bindings(root: Path = ROOT) -> StaticBindings:
    paths = {
        "compose": root / COMPOSE_RELATIVE,
        "config": root / CONFIG_RELATIVE,
        "package": root / PACKAGE_RELATIVE,
        "lock": root / LOCK_RELATIVE,
        "adapter": root / ADAPTER_RELATIVE,
        "spike": root / SPIKE_RELATIVE,
        "probe": root / "scripts/probe_livekit_broadcast_runtime.py",
    }
    try:
        compose_text = paths["compose"].read_text(encoding="utf-8")
        config_text = paths["config"].read_text(encoding="utf-8")
        adapter_text = paths["adapter"].read_text(encoding="utf-8")
        package = _load_json(paths["package"])
        lock = _load_json(paths["lock"])
        source_digests = {name: _sha256_file(path) for name, path in paths.items()}
    except (OSError, UnicodeError, json.JSONDecodeError, ProbeFailure) as exc:
        raise ProbeFailure("static_binding_input_unavailable") from exc

    image_match = re.search(r"^\s*image:\s*(livekit/livekit-server@sha256:[0-9a-f]{64})\s*$", compose_text, re.MULTILINE)
    image_reference = image_match.group(1) if image_match else None
    dependencies = package.get("dependencies")
    client_package = dependencies.get("livekit-client") if isinstance(dependencies, Mapping) else None
    lock_packages = lock.get("packages")
    locked = lock_packages.get("node_modules/livekit-client") if isinstance(lock_packages, Mapping) else None
    client_lock = locked.get("version") if isinstance(locked, Mapping) else None
    integrity = locked.get("integrity") if isinstance(locked, Mapping) else None

    reasons: list[str] = []
    if image_reference != EXPECTED_IMAGE_REFERENCE:
        reasons.append("server_image_digest_drift")
    if client_package != EXPECTED_CLIENT_VERSION:
        reasons.append("client_package_version_drift")
    if client_lock != EXPECTED_CLIENT_VERSION or not isinstance(integrity, str) or not integrity.startswith("sha512-"):
        reasons.append("client_lock_binding_invalid")
    if "--config" not in compose_text or "/etc/livekit/livekit.yaml" not in compose_text:
        reasons.append("server_config_mount_binding_missing")
    if not re.search(
        rf"(?m)^\s*max_participants:\s*{EXPECTED_ROOM_PARTICIPANT_CEILING}\s*$",
        config_text,
    ):
        reasons.append("four_peer_room_capacity_not_bound")
    if not _client_stream_policy_bound(adapter_text):
        reasons.append("client_stream_policy_binding_missing")
    return StaticBindings(
        image_reference=image_reference,
        client_package_version=str(client_package) if client_package is not None else None,
        client_lock_version=str(client_lock) if client_lock is not None else None,
        client_lock_integrity=str(integrity) if integrity is not None else None,
        config_sha256=_sha256_bytes(config_text.encode("utf-8")),
        source_sha256=_canonical_sha256(source_digests),
        source_digests=source_digests,
        config_text=config_text,
        adapter_text=adapter_text,
        reason_codes=tuple(sorted(set(reasons))),
    )


def _evidence(kind: str, locator: str, digest: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"kind": kind, "locator": locator}
    if digest is not None:
        row["sha256"] = digest
    return row


def _capability_matrix(bindings: StaticBindings, runtime: RuntimeObservation | None) -> list[dict[str, Any]]:
    trusted = runtime is not None and runtime.trusted
    api_reference = "https://docs.livekit.io/reference/other/roomservice-api/"
    data_reference = "https://docs.livekit.io/transport/data/packets/"
    distributed_reference = "https://docs.livekit.io/transport/self-hosting/distributed/"
    rows: list[dict[str, Any]] = [
        {
            "capability": "room_service_update_subscriptions",
            "status": "available" if trusted and runtime.room_service_update_subscriptions else "degraded",
            "required_for_feasibility": True,
            "evidence": [_evidence("public_documentation_url", api_reference)],
        },
        {
            "capability": "room_service_send_data",
            "status": "available" if trusted and runtime.room_service_send_data else "degraded",
            "required_for_feasibility": True,
            "evidence": [_evidence("public_documentation_url", api_reference)],
        },
        {
            "capability": "participant_permissions",
            "status": "available" if trusted and runtime.participant_permissions else "degraded",
            "required_for_feasibility": True,
            "evidence": [_evidence("public_documentation_url", api_reference)],
        },
        {
            "capability": "publisher_subscription_permissions_defense_in_depth",
            "status": "documented" if "setTrackSubscriptionPermissions" in bindings.adapter_text else "unsupported",
            "required_for_feasibility": False,
            "evidence": [_evidence("repository_file", ADAPTER_RELATIVE.as_posix(), bindings.source_digests["adapter"])],
        },
        {
            "capability": "simulcast_svc_track_publish_options",
            "status": "documented",
            "required_for_feasibility": False,
            "evidence": [_evidence("pinned_client_package", PACKAGE_RELATIVE.as_posix(), bindings.source_digests["package"])],
        },
        {
            "capability": "adaptive_stream_dynacast",
            "status": "documented" if _client_stream_policy_bound(bindings.adapter_text) else "unsupported",
            "required_for_feasibility": False,
            "evidence": [_evidence("repository_file", ADAPTER_RELATIVE.as_posix(), bindings.source_digests["adapter"])],
        },
        {
            "capability": "browser_e2ee",
            "status": "available" if trusted and runtime.e2ee else "degraded",
            "required_for_feasibility": True,
            "evidence": [_evidence("repository_file", SPIKE_RELATIVE.as_posix(), bindings.source_digests["spike"])],
        },
        {
            "capability": "one_publisher_three_receiver_fanout",
            "status": "available" if trusted and runtime.publisher_count == 1 and runtime.receiver_count == 3 and runtime.decoded_receiver_count == 3 else "degraded",
            "required_for_feasibility": True,
            "evidence": [_evidence("repository_file", SPIKE_RELATIVE.as_posix(), bindings.source_digests["spike"])],
        },
        {
            "capability": "data_packet_limits",
            "status": "degraded",
            "required_for_feasibility": False,
            "limits": {"reliable_payload_bytes": 15360, "lossy_payload_bytes": 1300},
            "evidence": [
                _evidence("public_documentation_url", data_reference),
                _evidence("repository_file", ADAPTER_RELATIVE.as_posix(), bindings.source_digests["adapter"]),
            ],
            "reason": "client guard permits 16384 bytes and is wider than the documented reliable payload boundary",
        },
        {
            "capability": "prometheus_metrics",
            "status": "documented" if re.search(r"^\s*prometheus_port:", bindings.config_text, re.MULTILINE) else "unsupported",
            "required_for_feasibility": False,
            "evidence": [_evidence("repository_file", CONFIG_RELATIVE.as_posix(), bindings.source_digests["config"])],
        },
        {
            "capability": "native_drain",
            "status": "documented",
            "required_for_feasibility": False,
            "evidence": [_evidence("public_documentation_url", distributed_reference)],
        },
        {
            "capability": "embedded_turn",
            "status": "documented" if re.search(r"(?ms)^turn:\s*\n\s+enabled:\s+true\s*$", bindings.config_text) else "unsupported",
            "required_for_feasibility": False,
            "evidence": [_evidence("repository_file", CONFIG_RELATIVE.as_posix(), bindings.source_digests["config"])],
        },
        {
            "capability": "distributed_placement",
            "status": "documented" if re.search(r"(?m)^redis:", bindings.config_text) else "unsupported",
            "required_for_feasibility": False,
            "owner": PLACEMENT_OWNER,
            "evidence": [
                _evidence("public_documentation_url", distributed_reference),
                _evidence("repository_file", CONFIG_RELATIVE.as_posix(), bindings.source_digests["config"]),
            ],
        },
        {
            "capability": "custom_jwt_claim_audience_enforcement",
            "status": "unsupported",
            "required_for_feasibility": False,
            "reason": "custom claims are not treated as LiveKit server authorization",
            "evidence": [],
        },
        {
            "capability": "runtime_route_epoch_queue_fencing",
            "status": "unsupported",
            "required_for_feasibility": False,
            "reason": "Hub-owned semantics are not native LiveKit runtime guarantees",
            "evidence": [],
        },
        {
            "capability": "authenticated_runtime_extension",
            "status": "unsupported",
            "required_for_feasibility": False,
            "reason": "not selected; no private hooks are permitted",
            "evidence": [],
        },
    ]
    if trusted:
        live = _evidence("live_container_smoke", "probe-owned Docker and browser session", runtime.smoke_report_sha256)
        for row in rows:
            if row["status"] == "available":
                row["evidence"].append(live)
    return rows


def build_report(bindings: StaticBindings, runtime: RuntimeObservation | None = None) -> dict[str, Any]:
    capabilities = _capability_matrix(bindings, runtime)
    reasons = list(bindings.reason_codes)
    if runtime is None:
        reasons.append("runtime_evidence_missing")
    elif not runtime.trusted:
        reasons.extend(runtime.reason_codes or ("runtime_evidence_untrusted",))
    required_missing = sorted(
        row["capability"]
        for row in capabilities
        if row["required_for_feasibility"] and row["status"] != "available"
    )
    if required_missing:
        reasons.append("required_runtime_capability_unavailable")
    reasons = sorted(set(reasons))
    return {
        "schema": "ananta.livekit-broadcast-runtime-capabilities.v1",
        "gate_id": "SFB-BASE-006",
        "decision": "go" if not reasons else "blocked",
        "runtime_control_mode": RUNTIME_CONTROL_MODE,
        "placement_owner": PLACEMENT_OWNER,
        "feature_defaults": {
            "sfu_broadcast_enabled": False,
            "authenticated_runtime_extension_enabled": False,
            "hub_livekit_node_selection_enabled": False,
        },
        "version_binding": {
            "server": {
                "expected_version": EXPECTED_SERVER_VERSION,
                "image_reference": bindings.image_reference,
                "expected_image_digest": EXPECTED_IMAGE_DIGEST,
                "runtime_version": runtime.server_version if runtime else None,
                "runtime_image_reference": runtime.image_reference if runtime else None,
                "runtime_image_id_sha256": runtime.image_id_sha256 if runtime else None,
            },
            "browser_sdk": {
                "package": "livekit-client",
                "expected_version": EXPECTED_CLIENT_VERSION,
                "package_version": bindings.client_package_version,
                "lock_version": bindings.client_lock_version,
                "lock_integrity": bindings.client_lock_integrity,
            },
            "server_sdk": {
                "package": None,
                "version": None,
                "binding": "not_used_documented_public_twirp_json",
            },
            "config_sha256": bindings.config_sha256,
            "runtime_config_sha256": runtime.config_sha256 if runtime else None,
            "source_sha256": bindings.source_sha256,
        },
        "runtime_evidence": None if runtime is None else {
            "provenance": runtime.provenance,
            "container_id_sha256": runtime.container_id_sha256,
            "smoke_report_sha256": runtime.smoke_report_sha256,
            "publishers": runtime.publisher_count,
            "receivers": runtime.receiver_count,
            "publisher_uploads": runtime.publisher_upload_count,
            "publisher_publications": runtime.publisher_publication_count,
            "decoded_receivers": runtime.decoded_receiver_count,
            "cleanup_complete": runtime.cleanup_complete,
        },
        "capabilities": capabilities,
        "required_capabilities_unavailable": required_missing,
        "reason_codes": reasons,
    }


def _run(command: list[str], *, root: Path, env: Mapping[str, str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=root, env=dict(env), capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        raise ProbeFailure(f"runtime_command_failed:{Path(command[0]).name}")
    return result


def _jwt(api_key: str, api_secret: str, room: str) -> str:
    now = int(time.time())
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = encode(json.dumps({
        "iss": api_key,
        "sub": "ananta-runtime-probe",
        "nbf": now - 5,
        "exp": now + 120,
        "video": {"roomAdmin": True, "room": room},
    }, separators=(",", ":")).encode())
    unsigned = f"{header}.{payload}"
    signature = encode(hmac.new(api_secret.encode(), unsigned.encode(), hashlib.sha256).digest())
    return f"{unsigned}.{signature}"


def _twirp(base_url: str, method: str, token: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    request = Request(
        f"{base_url}/twirp/livekit.RoomService/{method}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            decoded = json.loads(response.read().decode("utf-8") or "{}")
    except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeFailure(f"room_service_{method.lower()}_failed") from exc
    if not isinstance(decoded, Mapping):
        raise ProbeFailure(f"room_service_{method.lower()}_shape_invalid")
    return decoded


def _wait_for_live_room(base_url: str, token: str, process: subprocess.Popen[str], timeout: int) -> str:
    deadline = time.monotonic() + timeout
    expected = {"publisher", "receiver-1", "receiver-2", "receiver-3"}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ProbeFailure("browser_smoke_ended_before_control_probe")
        try:
            response = _twirp(base_url, "ListParticipants", token, {"room": ROOM_NAME})
        except ProbeFailure:
            time.sleep(0.25)
            continue
        participants = response.get("participants")
        if not isinstance(participants, list):
            time.sleep(0.25)
            continue
        identities = {row.get("identity") for row in participants if isinstance(row, Mapping)}
        publisher = next((row for row in participants if isinstance(row, Mapping) and row.get("identity") == "publisher"), None)
        tracks = publisher.get("tracks") if isinstance(publisher, Mapping) else None
        track = next((row for row in tracks or [] if isinstance(row, Mapping) and row.get("name") == "fanout-camera"), None)
        track_sid = track.get("sid") if isinstance(track, Mapping) else None
        if expected.issubset(identities) and isinstance(track_sid, str) and track_sid:
            return track_sid
        time.sleep(0.25)
    raise ProbeFailure("live_room_topology_timeout")


def validate_spike_report(report: Mapping[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    pinned = report.get("pinned")
    topology = report.get("topology")
    engines = report.get("engines")
    if (
        report.get("schema") != "ananta.semantic-sfu-three-peer-spike.v1"
        or report.get("verdict") != "pass"
        or report.get("release_evidence") is not False
    ):
        reasons.append("browser_smoke_verdict_invalid")
    if not isinstance(pinned, Mapping) or (
        pinned.get("server_version") != EXPECTED_SERVER_VERSION
        or pinned.get("server_digest") != EXPECTED_IMAGE_DIGEST
        or pinned.get("client_version") != EXPECTED_CLIENT_VERSION
    ):
        reasons.append("browser_smoke_version_binding_invalid")
    if (
        not isinstance(topology, Mapping)
        or topology.get("publishers") != 1
        or topology.get("receivers") != 3
        or topology.get("expected_publisher_publications") != 1
    ):
        reasons.append("browser_smoke_topology_invalid")
    if not isinstance(engines, list) or not engines:
        reasons.append("browser_smoke_engine_evidence_missing")
    else:
        for engine in engines:
            peers = engine.get("peers") if isinstance(engine, Mapping) else None
            if not isinstance(peers, list):
                reasons.append("browser_smoke_peer_evidence_invalid")
                continue
            publisher = next((row for row in peers if isinstance(row, Mapping) and row.get("identity") == "publisher"), None)
            receivers = [row for row in peers if isinstance(row, Mapping) and str(row.get("identity", "")).startswith("receiver-")]
            if (
                not isinstance(publisher, Mapping)
                or publisher.get("peer_connections") != 1
                or publisher.get("local_video_publication_count") != 1
                or int(publisher.get("outbound_video_streams") or 0) < 1
                or int(publisher.get("outbound_video_bytes") or 0) < 1
                or len(receivers) != 3
                or any(int(row.get("decoded_samples") or 0) < 3 or int(row.get("inbound_video_bytes") or 0) < 1 for row in receivers)
            ):
                reasons.append("browser_smoke_media_evidence_invalid")
    e2ee = report.get("e2ee")
    if not isinstance(e2ee, Mapping) or e2ee.get("enabled") is not True or e2ee.get("server_plaintext_access") is not False:
        reasons.append("browser_smoke_e2ee_evidence_invalid")
    return tuple(sorted(set(reasons)))


def execute_runtime_probe(bindings: StaticBindings, root: Path = ROOT, timeout: int = 90) -> RuntimeObservation:
    reasons: list[str] = list(bindings.reason_codes)
    image_reference = image_id = server_version = runtime_config_sha = container_hash = smoke_hash = None
    publisher_count = receiver_count = upload_count = publication_count = decoded_count = 0
    update_ok = send_ok = permission_ok = e2ee_ok = cleanup_ok = False
    provenance = "unavailable"
    process: subprocess.Popen[str] | None = None
    project = f"ananta-livekit-runtime-{secrets.token_hex(6)}"
    api_key = f"probe-{secrets.token_hex(8)}"
    api_secret = secrets.token_urlsafe(48)
    env = dict(os.environ)
    env.update({
        "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY": api_key,
        "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET": api_secret,
        "ANANTA_SEMANTIC_MEDIA_TURN_GATE_USER": f"probe-{secrets.token_hex(8)}",
        "ANANTA_SEMANTIC_MEDIA_TURN_GATE_PASSWORD": secrets.token_urlsafe(48),
        "ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL": "ws://127.0.0.1:7880",
        "ANANTA_SEMANTIC_MEDIA_SFU_BROWSER_ENGINES": "chromium",
        "ANANTA_SEMANTIC_MEDIA_SFU_RECEIVER_COUNT": "3",
        "ANANTA_SEMANTIC_MEDIA_SFU_WRONG_KEY_PROBE": "1",
    })
    compose = ["docker", "compose", "-p", project, "-f", str(root / COMPOSE_RELATIVE), "--profile", "semantic-media-sfu"]
    if bindings.reason_codes:
        reasons.append("static_binding_failed_before_runtime")
    elif shutil.which("docker") is None or shutil.which("node") is None:
        reasons.append("runtime_tooling_unavailable")
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="ananta-livekit-runtime-") as temporary:
                output = Path(temporary) / "smoke.json"
                mounted = Path(temporary) / "livekit.yaml"
                env["ANANTA_SEMANTIC_MEDIA_SFU_SPIKE_OUTPUT"] = str(output)
                _run([*compose, "up", "-d", "--wait", "semantic-media-sfu"], root=root, env=env, timeout=120)
                container_id = _run([*compose, "ps", "-q", "semantic-media-sfu"], root=root, env=env).stdout.strip()
                if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
                    raise ProbeFailure("runtime_container_identity_invalid")
                provenance = "live_container"
                container_hash = _sha256_bytes(container_id.encode())
                image_reference = _run(["docker", "inspect", "--format", "{{.Config.Image}}", container_id], root=root, env=env).stdout.strip()
                image_id = _run(["docker", "inspect", "--format", "{{.Image}}", container_id], root=root, env=env).stdout.strip()
                if image_reference != EXPECTED_IMAGE_REFERENCE or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                    raise ProbeFailure("runtime_image_digest_unverified")
                version_result = _run(["docker", "exec", container_id, "/livekit-server", "--version"], root=root, env=env)
                version_output = f"{version_result.stdout}\n{version_result.stderr}"
                match = re.search(r"(?<![0-9])(\d+\.\d+\.\d+)(?![0-9])", version_output)
                server_version = match.group(1) if match else None
                if server_version != EXPECTED_SERVER_VERSION:
                    raise ProbeFailure("runtime_server_version_drift")
                _run(["docker", "cp", f"{container_id}:/etc/livekit/livekit.yaml", str(mounted)], root=root, env=env)
                runtime_config_sha = _sha256_file(mounted)
                if runtime_config_sha != bindings.config_sha256:
                    raise ProbeFailure("runtime_config_digest_drift")

                process = subprocess.Popen(
                    ["node", str(root / SPIKE_RELATIVE)],
                    cwd=root,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                token = _jwt(api_key, api_secret, ROOM_NAME)
                track_sid = _wait_for_live_room("http://127.0.0.1:7880", token, process, min(timeout, 45))
                _twirp("http://127.0.0.1:7880", "UpdateSubscriptions", token, {
                    "room": ROOM_NAME, "identity": "receiver-1", "track_sids": [track_sid], "subscribe": False,
                })
                _twirp("http://127.0.0.1:7880", "UpdateSubscriptions", token, {
                    "room": ROOM_NAME, "identity": "receiver-1", "track_sids": [track_sid], "subscribe": True,
                })
                update_ok = True
                _twirp("http://127.0.0.1:7880", "UpdateParticipant", token, {
                    "room": ROOM_NAME, "identity": "receiver-3", "permission": {"can_subscribe": True},
                })
                permission_ok = True
                _twirp("http://127.0.0.1:7880", "SendData", token, {
                    "room": ROOM_NAME,
                    "data": base64.b64encode(b"ananta-runtime-probe").decode("ascii"),
                    "kind": "RELIABLE",
                    "destination_identities": ["receiver-1", "receiver-2", "receiver-3"],
                    "topic": "ananta.control.v1",
                })
                send_ok = True
                try:
                    process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    process.terminate()
                    process.communicate(timeout=5)
                    raise ProbeFailure("browser_smoke_timeout") from exc
                if process.returncode != 0 or not output.is_file():
                    raise ProbeFailure("browser_smoke_process_failed")
                report = _load_json(output)
                reasons.extend(validate_spike_report(report))
                smoke_hash = _sha256_file(output)
                topology = report.get("topology")
                publisher_count = int(topology.get("publishers") or 0) if isinstance(topology, Mapping) else 0
                receiver_count = int(topology.get("receivers") or 0) if isinstance(topology, Mapping) else 0
                engines = report.get("engines")
                peers = engines[0].get("peers") if isinstance(engines, list) and engines and isinstance(engines[0], Mapping) else []
                publisher = next((row for row in peers if isinstance(row, Mapping) and row.get("identity") == "publisher"), {})
                receivers = [row for row in peers if isinstance(row, Mapping) and str(row.get("identity", "")).startswith("receiver-")]
                upload_count = int(publisher.get("peer_connections") or 0)
                publication_count = int(
                    publisher.get("local_video_publication_count") or 0
                )
                decoded_count = sum(int(row.get("decoded_samples") or 0) >= 3 for row in receivers)
                e2ee_ok = not validate_spike_report(report) and decoded_count == 3
        except (ProbeFailure, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            reasons.append(str(exc) if isinstance(exc, ProbeFailure) else "runtime_probe_internal_failure")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            try:
                cleanup = subprocess.run(
                    [*compose, "down", "--volumes", "--remove-orphans"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                cleanup_ok = cleanup.returncode == 0
            except (OSError, subprocess.SubprocessError):
                cleanup_ok = False
            if not cleanup_ok:
                reasons.append("runtime_cleanup_failed")
    return RuntimeObservation(
        provenance=provenance,
        image_reference=image_reference,
        image_id_sha256=image_id,
        server_version=server_version,
        config_sha256=runtime_config_sha,
        container_id_sha256=container_hash,
        smoke_report_sha256=smoke_hash,
        publisher_count=publisher_count,
        receiver_count=receiver_count,
        publisher_upload_count=upload_count,
        publisher_publication_count=publication_count,
        decoded_receiver_count=decoded_count,
        room_service_update_subscriptions=update_ok,
        room_service_send_data=send_ok,
        participant_permissions=permission_ok,
        e2ee=e2ee_ok,
        cleanup_complete=cleanup_ok,
        reason_codes=tuple(sorted(set(reasons))),
        _origin=_RUNTIME_ORIGIN,
    )


def probe(*, root: Path = ROOT, execute_runtime: bool = False, timeout: int = 90) -> dict[str, Any]:
    bindings = collect_static_bindings(root)
    runtime = execute_runtime_probe(bindings, root, timeout) if execute_runtime else None
    return build_report(bindings, runtime)


def _write(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the pinned LiveKit broadcast runtime boundary.")
    parser.add_argument("--execute-runtime", action="store_true", help="Own and execute the real Docker/browser smoke.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = probe(execute_runtime=args.execute_runtime, timeout=max(30, min(int(args.timeout), 300)))
    except ProbeFailure as exc:
        report = {
            "schema": "ananta.livekit-broadcast-runtime-capabilities.v1",
            "gate_id": "SFB-BASE-006",
            "decision": "blocked",
            "runtime_control_mode": RUNTIME_CONTROL_MODE,
            "placement_owner": PLACEMENT_OWNER,
            "feature_defaults": {"sfu_broadcast_enabled": False},
            "capabilities": [],
            "reason_codes": [str(exc)],
        }
    if not args.no_write:
        _write(args.output, report)
        print(json.dumps({"decision": report["decision"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["decision"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
