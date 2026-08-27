#!/usr/bin/env python3
"""Validate bounded acceptance evidence for optional training backends."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

BACKENDS = ("autotrain", "axolotl", "llamafactory", "torchtune")
STATUSES = frozenset({"verified", "not_run", "failed", "blocked"})


def validate_evidence(value: Mapping[str, Any]) -> None:
    expected = {
        "backend",
        "backend_version",
        "container_digest",
        "hardware",
        "result",
        "schema_version",
        "tests",
    }
    if set(value) != expected:
        raise ValueError("acceptance evidence contains unknown or missing fields")
    if value["schema_version"] != "ananta.training-backend-acceptance.v1":
        raise ValueError("unsupported acceptance evidence schema")
    if value["backend"] not in BACKENDS or value["result"] not in STATUSES:
        raise ValueError("unsupported backend or result")
    if not isinstance(value["tests"], list) or not value["tests"]:
        raise ValueError("acceptance evidence requires tests")
    for item in value["tests"]:
        if not isinstance(item, Mapping) or set(item) != {"name", "result"} or item["result"] not in STATUSES:
            raise ValueError("invalid acceptance test result")
    if value["result"] == "verified":
        digest = value["container_digest"]
        hardware = value["hardware"]
        if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
            raise ValueError("verified evidence requires a container digest")
        if not isinstance(hardware, Mapping) or not hardware.get("gpu") or not hardware.get("driver"):
            raise ValueError("verified evidence requires hardware attestation")
        if any(item["result"] != "verified" for item in value["tests"]):
            raise ValueError("verified evidence cannot contain incomplete tests")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--print-sha256", action="store_true")
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("acceptance evidence must be an object")
    validate_evidence(value)
    if args.print_sha256:
        print(hashlib.sha256(raw).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
