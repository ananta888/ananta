"""Ephemeral packet-capture boundary for semantic-media release evidence.

The module owns only capture lifecycle and bounded counter extraction. Raw
PCAP bytes stay in a caller-owned temporary directory and are never returned.
"""

from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_IMAGE = (
    "nicolaka/netshoot@sha256:"
    "a20c2531bf35436ed3766cd6cfe89d352b050ccc4d7005ce6400adf97503da1b"
)
KNOWN_MARKERS = (
    b"synthetic-control-marker",
    b"synthetic-transcript-marker",
    b"synthetic-recovery-marker",
)
_CAPTURE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_FILTER_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class PacketCaptureError(RuntimeError):
    """Bounded capture failure without packet or credential content."""


def start_container_capture(
    *,
    capture_name: str,
    target_container: str,
    capture_dir: Path,
    capture_path: str,
    capture_filter: Iterable[str],
) -> None:
    _start_capture(
        capture_name=capture_name,
        network=("container", target_container),
        capture_dir=capture_dir,
        capture_path=capture_path,
        capture_filter=tuple(capture_filter),
    )


def start_host_capture(
    *,
    capture_name: str,
    capture_dir: Path,
    capture_path: str,
    capture_filter: Iterable[str],
    interface: str = "lo",
) -> None:
    if interface not in {"lo", "any"}:
        raise PacketCaptureError("capture_interface_invalid")
    _start_capture(
        capture_name=capture_name,
        network=("host", ""),
        capture_dir=capture_dir,
        capture_path=capture_path,
        capture_filter=tuple(capture_filter),
        interface=interface,
    )


def _start_capture(
    *,
    capture_name: str,
    network: tuple[str, str],
    capture_dir: Path,
    capture_path: str,
    capture_filter: tuple[str, ...],
    interface: str = "any",
) -> None:
    _validate(capture_name, capture_path, capture_filter)
    capture_dir.chmod(0o777)
    pull = subprocess.run(
        ["docker", "pull", CAPTURE_IMAGE],
        cwd=ROOT,
        check=False,
        timeout=120,
        capture_output=True,
    )
    if pull.returncode != 0:
        raise PacketCaptureError("capture_image_unavailable")
    network_args = ["--network", "host"] if network[0] == "host" else [
        "--network",
        f"container:{network[1]}",
    ]
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            capture_name,
            *network_args,
            "--cap-add",
            "NET_RAW",
            "--cap-add",
            "NET_ADMIN",
            "-v",
            f"{capture_dir}:/capture",
            CAPTURE_IMAGE,
            "tcpdump",
            "-i",
            interface,
            "-U",
            "-s",
            "0",
            "-w",
            f"/capture/{capture_path}",
            *capture_filter,
        ],
        cwd=ROOT,
        check=False,
        timeout=60,
        capture_output=True,
    )
    if started.returncode != 0:
        raise PacketCaptureError("capture_start_failed")


def stop_capture(capture_name: str) -> None:
    if not _CAPTURE_NAME.fullmatch(capture_name):
        raise PacketCaptureError("capture_name_invalid")
    subprocess.run(
        ["docker", "kill", "--signal", "SIGINT", capture_name],
        cwd=ROOT,
        check=False,
        timeout=30,
        capture_output=True,
    )
    subprocess.run(
        ["docker", "wait", capture_name],
        cwd=ROOT,
        check=False,
        timeout=30,
        capture_output=True,
    )


def capture_measurements(
    path: Path,
    boundary: str,
    *,
    scan_credentials: bool = True,
) -> dict[str, int | bool]:
    if boundary not in {"hub", "sfu", "turn"}:
        raise PacketCaptureError("capture_boundary_invalid")
    data = path.read_bytes()
    if not 24 <= len(data) <= 128 * 1024 * 1024:
        raise PacketCaptureError("capture_size_invalid")
    packet_count = pcap_packet_count(data)
    if packet_count < 1:
        raise PacketCaptureError("capture_packets_missing")
    marker_matches = sum(data.count(marker) for marker in KNOWN_MARKERS)
    measurements: dict[str, int | bool] = {
        f"{boundary}_boundary_capture_verified": True,
        f"{boundary}_boundary_packet_count": packet_count,
        f"{boundary}_boundary_capture_bytes": len(data),
        f"{boundary}_boundary_known_marker_matches": marker_matches,
    }
    if scan_credentials:
        measurements[f"{boundary}_boundary_credential_matches"] = (
            data.count(b"-----BEGIN PRIVATE KEY-----") + data.count(b'"private_key"')
        )
    return measurements


def pcap_packet_count(data: bytes) -> int:
    magic = data[:4]
    endian = {
        b"\xd4\xc3\xb2\xa1": "<",
        b"\x4d\x3c\xb2\xa1": "<",
        b"\xa1\xb2\xc3\xd4": ">",
        b"\xa1\xb2\x3c\x4d": ">",
    }.get(magic)
    if endian is None:
        raise PacketCaptureError("capture_format_invalid")
    offset = 24
    packets = 0
    while offset < len(data):
        if offset + 16 > len(data):
            raise PacketCaptureError("capture_truncated")
        included = struct.unpack_from(f"{endian}I", data, offset + 8)[0]
        if included > 16 * 1024 * 1024 or offset + 16 + included > len(data):
            raise PacketCaptureError("capture_packet_invalid")
        offset += 16 + included
        packets += 1
    return packets


def _validate(capture_name: str, capture_path: str, capture_filter: tuple[str, ...]) -> None:
    if not _CAPTURE_NAME.fullmatch(capture_name):
        raise PacketCaptureError("capture_name_invalid")
    if not _CAPTURE_NAME.fullmatch(capture_path) or not capture_path.endswith(".pcap"):
        raise PacketCaptureError("capture_path_invalid")
    if not capture_filter or any(not _FILTER_TOKEN.fullmatch(token) for token in capture_filter):
        raise PacketCaptureError("capture_filter_invalid")


__all__ = [
    "CAPTURE_IMAGE",
    "PacketCaptureError",
    "capture_measurements",
    "pcap_packet_count",
    "start_container_capture",
    "start_host_capture",
    "stop_capture",
]
