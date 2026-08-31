#!/usr/bin/env python3
"""Fail-closed preflight and idempotent network provisioning for the room edge."""

from __future__ import annotations

import argparse
import os
import re
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WebrtcEdgeProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class WebrtcEdgeConfig:
    network: str
    room_container: str
    certificate: Path
    private_key: Path

    @classmethod
    def from_environment(cls) -> "WebrtcEdgeConfig":
        certificate = os.environ.get("ANANTA_WEBRTC_TLS_CERT_FILE", "")
        private_key = os.environ.get("ANANTA_WEBRTC_TLS_KEY_FILE", "")
        return cls(
            network=os.environ.get("ANANTA_WEBRTC_EDGE_NETWORK", "webrtc-edge"),
            room_container=os.environ.get("ANANTA_WEBRTC_ROOM_CONTAINER", "webrtc-room-server"),
            certificate=Path(certificate),
            private_key=Path(private_key),
        )

    def validate(self) -> None:
        if not _SAFE_NAME.fullmatch(self.network) or not _SAFE_NAME.fullmatch(self.room_container):
            raise WebrtcEdgeProvisionError("webrtc_edge_name_invalid")
        for path, reason in (
            (self.certificate, "webrtc_edge_certificate_missing"),
            (self.private_key, "webrtc_edge_private_key_missing"),
        ):
            if not path.is_absolute() or not path.is_file() or path.is_symlink():
                raise WebrtcEdgeProvisionError(reason)
        try:
            ssl._ssl._test_decode_cert(str(self.certificate))  # noqa: SLF001
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(self.certificate), str(self.private_key))
        except (OSError, ssl.SSLError) as exc:
            raise WebrtcEdgeProvisionError("webrtc_edge_certificate_key_invalid") from exc
        if self.private_key.stat().st_mode & 0o077:
            raise WebrtcEdgeProvisionError("webrtc_edge_private_key_permissions_unsafe")


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603


def provision(config: WebrtcEdgeConfig, *, apply: bool, runner: Runner = _run) -> tuple[tuple[str, ...], ...]:
    config.validate()
    commands: list[tuple[str, ...]] = []
    network_inspect = runner(("docker", "network", "inspect", config.network))
    if network_inspect.returncode != 0:
        create = ("docker", "network", "create", config.network)
        commands.append(create)
        if apply and runner(create).returncode != 0:
            raise WebrtcEdgeProvisionError("webrtc_edge_network_create_failed")
    container_inspect = runner(("docker", "container", "inspect", config.room_container))
    if container_inspect.returncode != 0:
        raise WebrtcEdgeProvisionError("webrtc_edge_room_container_missing")
    connection_inspect = runner(
        ("docker", "network", "inspect", config.network, "--format", "{{json .Containers}}")
    )
    if config.room_container not in connection_inspect.stdout:
        connect = ("docker", "network", "connect", config.network, config.room_container)
        commands.append(connect)
        if apply and runner(connect).returncode != 0:
            raise WebrtcEdgeProvisionError("webrtc_edge_network_connect_failed")
    return tuple(commands)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Create/connect the network after the preflight")
    args = parser.parse_args(argv)
    try:
        commands = provision(WebrtcEdgeConfig.from_environment(), apply=args.apply)
    except WebrtcEdgeProvisionError as exc:
        print(str(exc))
        return 1
    for command in commands:
        print(" ".join(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
