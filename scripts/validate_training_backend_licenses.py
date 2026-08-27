#!/usr/bin/env python3
"""Validate the closed, version-bound optional training-backend register."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HTTPS = re.compile(r"^https://[^\s]+$")
_LOCK_ENTRY = re.compile(r"^[A-Za-z0-9_.-]+==[^\s]+(?:\s*;.*)?$")


def validate(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["register must be an object"]
    problems: list[str] = []
    if payload.get("schema_version") != "ananta.training-backend-licenses.v1":
        problems.append("schema_version is unsupported")
    allowed = payload.get("allowed_spdx_ids")
    if not isinstance(allowed, list) or not allowed or any(not isinstance(item, str) for item in allowed):
        problems.append("allowed_spdx_ids must be a non-empty string list")
        allowed_set: set[str] = set()
    else:
        allowed_set = set(allowed)
    backends = payload.get("backends")
    if not isinstance(backends, list) or not backends:
        return [*problems, "backends must be a non-empty list"]
    seen: set[str] = set()
    for index, backend in enumerate(backends):
        prefix = f"backends[{index}]"
        if not isinstance(backend, dict):
            problems.append(f"{prefix} must be an object")
            continue
        backend_id = backend.get("id")
        if not isinstance(backend_id, str) or backend_id in seen:
            problems.append(f"{prefix}.id is missing or duplicated")
        else:
            seen.add(backend_id)
        if backend.get("license_spdx") not in allowed_set:
            problems.append(f"{prefix}.license_spdx is not allowed")
        if backend.get("maintenance") not in {"active", "unmaintained"}:
            problems.append(f"{prefix}.maintenance is invalid")
        if backend.get("default_enabled") is not False:
            problems.append(f"{prefix}.default_enabled must be false")
        if not isinstance(backend.get("version"), str) or not backend["version"]:
            problems.append(f"{prefix}.version is missing")
        for field in ("license_url", "release_url"):
            if not isinstance(backend.get(field), str) or not _HTTPS.fullmatch(backend[field]):
                problems.append(f"{prefix}.{field} must be an HTTPS URL")
        source_commit = backend.get("source_commit")
        package_sha256 = backend.get("package_sha256")
        if not (
            isinstance(source_commit, str)
            and _COMMIT.fullmatch(source_commit)
            or isinstance(package_sha256, str)
            and _SHA256.fullmatch(package_sha256)
        ):
            problems.append(f"{prefix} needs a source commit or package SHA-256")
    policy = payload.get("policy")
    if not isinstance(policy, dict) or policy.get("unknown_license_action") != "deny":
        problems.append("unknown licenses must be denied")
    return problems


def validate_dependency_locks(payload: Any, root: Path) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("backends"), list):
        return ["cannot validate dependency locks without a backend register"]
    problems: list[str] = []
    for backend in payload["backends"]:
        if not isinstance(backend, dict):
            continue
        backend_id = backend.get("id")
        package_name = "autotrain-advanced" if backend_id == "autotrain" else backend_id
        version = backend.get("version")
        path = root / f"requirements.training-{backend_id}.lock.txt"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            problems.append(f"{backend_id} dependency lock is missing")
            continue
        entries = [line for line in lines if line and not line.startswith(("#", " "))]
        if not entries or any(not _LOCK_ENTRY.fullmatch(line) for line in entries):
            problems.append(f"{backend_id} dependency lock contains an unpinned entry")
        expected = f"{package_name}=={version}".casefold()
        if expected not in {line.split(";", 1)[0].strip().casefold() for line in entries}:
            problems.append(f"{backend_id} dependency lock does not match the reviewed version")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="config/licenses/training-backends.v1.json")
    args = parser.parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    problems = [
        *validate(payload),
        *validate_dependency_locks(payload, Path("docker/compose-next")),
    ]
    if problems:
        for problem in problems:
            print(problem)
        return 2
    print("training-backend-license-register-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
