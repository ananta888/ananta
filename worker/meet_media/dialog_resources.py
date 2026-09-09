"""Bounded numeric cgroup sampling; no host process discovery or GPU claims."""

import hmac
import time
from pathlib import Path

from ananta_contracts.meet_dialog import parse
from ananta_contracts.meet_dialog_resources import request_signature, validate_observation, validate_query


def _read_cgroup(name):
    with (Path("/sys/fs/cgroup") / name).open("rb") as source:
        value = source.read(1025)
    if len(value) > 1024:
        raise ValueError("meet_dialog_resources_counter_unavailable")
    return value.decode("ascii").strip()


def _number(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("meet_dialog_resources_counter_unavailable")
    result = int(value)
    if not 0 <= result < 2**53:
        raise ValueError("meet_dialog_resources_counter_unavailable")
    return result


def cgroup_snapshot(read=_read_cgroup):
    result = {}
    for field, filename in (
        ("memory_bytes", "memory.current"),
        ("memory_limit_bytes", "memory.max"),
        ("cpu_usage_us", "cpu.stat"),
        ("pids", "pids.current"),
    ):
        try:
            value = read(filename)
            if field == "cpu_usage_us":
                values = [line.split()[1:] for line in value.splitlines() if line.split()[:1] == ["usage_usec"]]
                if len(values) != 1 or len(values[0]) != 1:
                    raise ValueError("meet_dialog_resources_counter_unavailable")
                value = values[0][0]
            result[field] = _number(value)
        except (OSError, UnicodeError, ValueError):
            # Includes unlimited memory.max="max". No zero/free-memory fiction.
            result[field] = None
    return result


def observe_resources(key, body, supplied, executor, *, sample=None, clock=time.monotonic):
    if executor is None or not hmac.compare_digest(request_signature(key, body), supplied):
        raise ValueError("meet_dialog_resources_unauthorized")
    query = validate_query(parse(body))
    value = {
        "schema": "ananta.meet-dialog-resources.v1",
        "nonce": query["nonce"],
        "sampled_monotonic_us": int(clock() * 1_000_000),
        "slots": executor.slots.snapshot(),
        "cgroup": (cgroup_snapshot if sample is None else sample)(),
    }
    return validate_observation(value, query["nonce"])
