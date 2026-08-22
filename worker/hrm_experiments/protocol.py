"""Bounded length-prefixed JSON protocol for the networkless HRM runner."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any, Mapping

MAX_HRM_RUNNER_MESSAGE_BYTES = 2 * 1024 * 1024


class HrmRunnerProtocolError(RuntimeError):
    pass


def receive_message(connection: socket.socket) -> dict[str, Any]:
    header = _receive_exact(connection, 4)
    length = struct.unpack("!I", header)[0]
    if length < 2 or length > MAX_HRM_RUNNER_MESSAGE_BYTES:
        raise HrmRunnerProtocolError("hrm.runner_message_size_invalid")
    raw = _receive_exact(connection, length)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HrmRunnerProtocolError("hrm.runner_message_invalid") from exc
    if not isinstance(value, dict):
        raise HrmRunnerProtocolError("hrm.runner_message_invalid")
    return value


def send_message(connection: socket.socket, value: Mapping[str, Any]) -> None:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(raw) > MAX_HRM_RUNNER_MESSAGE_BYTES:
        raise HrmRunnerProtocolError("hrm.runner_message_too_large")
    connection.sendall(struct.pack("!I", len(raw)) + raw)


def _receive_exact(connection: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise HrmRunnerProtocolError("hrm.runner_connection_closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


__all__ = [
    "HrmRunnerProtocolError",
    "MAX_HRM_RUNNER_MESSAGE_BYTES",
    "receive_message",
    "send_message",
]
