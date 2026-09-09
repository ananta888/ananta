"""Closed read-only Worker observations; never a Hub capacity reservation."""

import hashlib
import hmac
import re


def request_signature(key, body):
    return hmac.new(key, b"ananta-meet-dialog-resources-request-v1\0" + body, hashlib.sha256).hexdigest()


def response_signature(key, request, body):
    return hmac.new(
        key, b"ananta-meet-dialog-resources-response-v1\0" + request + b"\0" + body, hashlib.sha256
    ).hexdigest()


def validate_query(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "nonce"}
        or value["schema"] != "ananta.meet-dialog-resources-query.v1"
        or not isinstance(value["nonce"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
    ):
        raise ValueError("meet_dialog_resources_query_invalid")
    return value


def _count(value):
    return type(value) is int and 0 <= value < 2**53


def validate_observation(value, nonce):
    if (
        not isinstance(nonce, str)
        or not re.fullmatch(r"[a-f0-9]{32}", nonce)
        or not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "sampled_monotonic_us", "slots", "cgroup"}
        or value["schema"] != "ananta.meet-dialog-resources.v1"
        or value["nonce"] != nonce
        or not _count(value["sampled_monotonic_us"])
    ):
        raise ValueError("meet_dialog_resources_invalid")
    slots, cgroup = value["slots"], value["cgroup"]
    if (
        not isinstance(slots, dict)
        or set(slots) != {"capacity", "active"}
        or not _count(slots["capacity"])
        or not 1 <= slots["capacity"] <= 4
        or not _count(slots["active"])
        or slots["active"] > slots["capacity"]
        or not isinstance(cgroup, dict)
        or set(cgroup) != {"memory_bytes", "memory_limit_bytes", "cpu_usage_us", "pids"}
        or any(item is not None and not _count(item) for item in cgroup.values())
    ):
        raise ValueError("meet_dialog_resources_invalid")
    return value | {"slots": dict(slots), "cgroup": dict(cgroup)}
