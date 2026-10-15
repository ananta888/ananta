"""Offline installed-version inventory; this does not attest wheel or image bytes."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)\s*\\?$")


def inventory(lock: bytes, *, lookup=importlib.metadata.version, python_version=None) -> dict:
    if not lock or len(lock) > 1024 * 1024:
        raise ValueError("jeff_dependency_lock_budget")
    pins = {}
    for line in lock.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "--hash=")):
            continue
        match = _PIN.fullmatch(stripped)
        if match is None or match[1] in pins:
            raise ValueError("jeff_dependency_lock_invalid")
        pins[match[1]] = match[2]
    if not pins:
        raise ValueError("jeff_dependency_lock_empty")
    observed, missing, mismatched = {}, [], []
    for name, expected in sorted(pins.items()):
        try:
            observed[name] = lookup(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if observed[name] != expected:
            mismatched.append(name)
    actual_python = tuple(python_version or sys.version_info[:2])
    return {
        "schema": "ananta.jeff-dependency-inventory.v1",
        "status": "compatible" if not missing and not mismatched and actual_python == (3, 12) else "blocked",
        "classification": "technical_observation",
        "production_release_evidence": False,
        "verification": "installed_versions_only_not_wheel_or_image_attestation",
        "lock_sha256": hashlib.sha256(lock).hexdigest(),
        "python": {"expected": [3, 12], "actual": list(actual_python)},
        "installed_versions": observed,
        "missing": missing,
        "mismatched": mismatched,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    with args.lock.open("rb") as stream:
        lock = stream.read(1024 * 1024 + 1)
    result = inventory(lock)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "compatible" else 2


if __name__ == "__main__":
    raise SystemExit(main())
