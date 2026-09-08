"""Untrusted terminal observations: closed numbers/reasons, never authority."""

import hashlib
import hmac
import json
import math
import re
from types import MappingProxyType

MAX_REQUEST_BYTES = 2048
MAX_RESPONSE_BYTES = 512
STOP_REASONS = frozenset(
    {"assignment_elapsed", "control_stale", "hub_unavailable_or_revoked", "session_failed", "runtime_failed"}
)
MEASUREMENT_LIMITS = MappingProxyType(
    {
        "elapsed_ms": 7_400_000,
        "self_cpu_ms": 500_000_000,
        "terminated_children_cpu_ms": 500_000_000,
        "self_max_rss_kib": 16_777_216,
        "terminated_child_max_rss_kib": 16_777_216,
    }
)
IDENTITY_FIELDS = ("task_id", "lease_id", "runtime_id")


def validate_observation(value):
    if type(value) is not dict or set(value) != {"schema", "stop_reason", "measurements"}:
        raise ValueError("meet_dialog_diagnostics_invalid")
    if (
        value["schema"] != "ananta.meet-dialog-terminal-observation.v1"
        or type(value["stop_reason"]) is not str
        or value["stop_reason"] not in STOP_REASONS
    ):
        raise ValueError("meet_dialog_diagnostics_invalid")
    measurements = value["measurements"]
    if measurements is not None:
        if type(measurements) is not dict or set(measurements) != set(MEASUREMENT_LIMITS):
            raise ValueError("meet_dialog_diagnostics_invalid")
        if any(
            type(measurements[k]) is not int or not 0 <= measurements[k] <= maximum
            for k, maximum in MEASUREMENT_LIMITS.items()
        ):
            raise ValueError("meet_dialog_diagnostics_invalid")
        measurements = dict(measurements)
    return {"schema": value["schema"], "stop_reason": value["stop_reason"], "measurements": measurements}


def validate_request(value, now):
    if type(value) is not dict or set(value) != {"schema", "nonce", "sent_at", "observation", *IDENTITY_FIELDS}:
        raise ValueError("meet_dialog_diagnostics_request_invalid")
    if value["schema"] != "ananta.meet-dialog-diagnostics-request.v1":
        raise ValueError("meet_dialog_diagnostics_request_invalid")
    if any(type(value[k]) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[k]) for k in IDENTITY_FIELDS):
        raise ValueError("meet_dialog_diagnostics_request_invalid")
    if type(value["nonce"]) is not str or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"]):
        raise ValueError("meet_dialog_diagnostics_request_invalid")
    if (
        type(now) not in {int, float}
        or not math.isfinite(now)
        or now <= 0
        or type(value["sent_at"]) is not int
        or not now - 10 <= value["sent_at"] <= now + 2
    ):
        raise ValueError("meet_dialog_diagnostics_expired")
    return value | {"observation": validate_observation(value["observation"])}


def observation_digest(value):
    closed = validate_observation(value)
    return hashlib.sha256(json.dumps(closed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request_signature(key, body):
    return hmac.new(key, b"meet-dialog-diagnostics-request-v1\0" + body, hashlib.sha256).hexdigest()


def response_signature(key, request_body, response_body):
    return hmac.new(
        key,
        b"meet-dialog-diagnostics-response-v1\0" + hashlib.sha256(request_body).digest() + response_body,
        hashlib.sha256,
    ).hexdigest()
