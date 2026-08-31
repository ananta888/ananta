"""Validation primitives for hub-owned semantic SFU admission requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

_ID_MAX_BYTES = 128


class SfuAdmissionError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def mutation_context(request: Mapping[str, Any]) -> tuple[str, str, int, int]:
    return (
        bounded_id(request.get("session_id"), "session_id"),
        bounded_id(request.get("idempotency_key"), "idempotency_key"),
        positive_int(request.get("membership_epoch"), "membership_epoch"),
        nonnegative_int(request.get("expected_revision"), "expected_revision"),
    )


def publication_limits(raw: Any, source: str) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise SfuAdmissionError("sfu_publication_constraints_invalid")
    defaults = {
        "microphone": (128_000, 0, 0, 0),
        "camera": (2_000_000, 1920, 1080, 30),
        "screen": (4_000_000, 2560, 1440, 30),
    }
    maximum = defaults[source]
    keys = ("max_bitrate_bps", "max_width", "max_height", "max_fps")
    values: list[int] = []
    for key, upper in zip(keys, maximum, strict=True):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > upper:
            raise SfuAdmissionError("sfu_publication_constraints_invalid")
        values.append(value)
    if values[0] < 6000 or (source != "microphone" and (values[1] < 1 or values[2] < 1 or values[3] < 1)):
        raise SfuAdmissionError("sfu_publication_constraints_invalid")
    return dict(zip(keys, values, strict=True))


def room_id(tenant_id: str, session_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\x1f{session_id}".encode()).hexdigest()[:32]
    return f"sfu-{digest}"


def bounded_id(value: Any, field: str) -> str:
    if not valid_id(value):
        raise SfuAdmissionError(f"sfu_{field}_invalid")
    return str(value)


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= _ID_MAX_BYTES and not value.isspace()


def positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SfuAdmissionError(f"sfu_{field}_invalid")
    return value


def nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SfuAdmissionError(f"sfu_{field}_invalid")
    return value


def exact_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise SfuAdmissionError(f"sfu_{field}_invalid")
    return value


def request_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()
