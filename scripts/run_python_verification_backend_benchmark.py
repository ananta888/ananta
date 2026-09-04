#!/usr/bin/env python3
"""Compare five shared properties on normal and solver-backed generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROPERTIES = (
    "test_clamp_stays_within_bounds",
    "test_identifier_normalization_is_idempotent",
    "test_unique_output_contains_no_duplicates",
    "test_unique_output_preserves_first_occurrence_order",
    "test_permission_allow_is_monotone",
)


def _run(backend: str) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--confcutdir=tests/verification",
        *(f"tests/verification/test_property_pilot.py::{name}" for name in PROPERTIES),
    ]
    env = {
        **os.environ,
        "ANANTA_HYPOTHESIS_BACKEND": backend,
        "ANANTA_HYPOTHESIS_CASES": "5",
        "PYTHONHASHSEED": "0",
    }
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False)
    return {
        "backend": backend,
        "duration_seconds": round(time.monotonic() - started, 3),
        "returncode": completed.returncode,
        "status": "passed" if completed.returncode == 0 else "tool_error",
        "properties": len(PROPERTIES),
    }


def main() -> int:
    observations = [_run("hypothesis"), _run("crosshair")]
    normal, symbolic = observations
    report = {
        "schema": "ananta.verification-backend-benchmark.v1",
        "observations": observations,
        "decision": "crosshair_backend_nightly_hold",
        "reason_code": (
            "solver_backend_materially_slower"
            if float(symbolic["duration_seconds"]) > float(normal["duration_seconds"]) * 2
            else "solver_backend_cost_comparable"
        ),
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if all(item["status"] == "passed" for item in observations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
