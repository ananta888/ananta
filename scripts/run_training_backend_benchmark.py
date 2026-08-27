#!/usr/bin/env python3
"""Validate and classify one real multi-backend training benchmark result."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_STATUSES = {"blocked", "failed", "not_run", "verified"}


def validate_result(payload: Any, matrix: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return ["result must be an object"]
    problems: list[str] = []
    backend_ids = {item["id"] for item in matrix["backends"]}
    if payload.get("backend") not in backend_ids:
        problems.append("backend is not in the benchmark matrix")
    status = payload.get("status")
    if status not in _STATUSES:
        problems.append("status is invalid")
    if status != "verified":
        if payload.get("metrics"):
            problems.append("unverified result cannot publish benchmark metrics")
        return problems
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict):
        return [*problems, "verified result needs bindings"]
    for field in matrix["required_bindings"]:
        value = bindings.get(field)
        if field == "seed":
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append("bindings.seed must be an integer")
        elif field == "container_digest":
            if not isinstance(value, str) or not _DIGEST.fullmatch(value):
                problems.append("bindings.container_digest is invalid")
        elif field == "backend_version":
            if not isinstance(value, str) or not value.strip():
                problems.append("bindings.backend_version is missing")
        elif not isinstance(value, str) or not _SHA256.fullmatch(value):
            problems.append(f"bindings.{field} is invalid")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return [*problems, "verified result needs metrics"]
    for field in matrix["required_metrics"]:
        value = metrics.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            problems.append(f"metrics.{field} is invalid")
    hardware = payload.get("hardware")
    if not isinstance(hardware, dict) or hardware.get("gpu_model") != "NVIDIA GeForce RTX 3080":
        problems.append("verified rtx3080-10gb result needs exact GPU attestation")
    if not isinstance(hardware, dict) or hardware.get("vram_bytes") != 10 * 1024**3:
        problems.append("verified rtx3080-10gb result needs 10-GiB VRAM attestation")
    return problems


def decision(payload: dict[str, Any], matrix: dict[str, Any]) -> str:
    metadata = next(item for item in matrix["backends"] if item["id"] == payload["backend"])
    if metadata["maintenance"] == "unmaintained":
        return "no-go"
    if payload["status"] == "verified":
        return "conditional-go"
    return "no-go"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default="benchmarks/training_backends/matrix.v1.json")
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    problems = validate_result(payload, matrix)
    if problems:
        for problem in problems:
            print(problem)
        return 2
    print(
        json.dumps(
            {"backend": payload["backend"], "status": payload["status"], "decision": decision(payload, matrix)},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
