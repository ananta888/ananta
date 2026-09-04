#!/usr/bin/env python3
"""Run the deterministic PR property suite repeatedly with a median budget."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--median-budget-seconds", type=float, default=8.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        raise SystemExit("repeat must be between 1 and 100")
    durations: list[float] = []
    for iteration in range(args.repeat):
        started = time.monotonic()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--confcutdir=tests/verification",
                "tests/verification/test_fast_gate.py",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        durations.append(time.monotonic() - started)
        if completed.returncode != 0:
            print(completed.stdout, end="")
            print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode
        print(f"verification-fast iteration={iteration + 1} duration={durations[-1]:.3f}s")
    report = {
        "schema": "ananta.verification-fast-gate.v1",
        "repeat": args.repeat,
        "failures": 0,
        "median_seconds": round(statistics.median(durations), 3),
        "max_seconds": round(max(durations), 3),
        "budget_seconds": args.median_budget_seconds,
        "status": "passed" if statistics.median(durations) <= args.median_budget_seconds else "failed",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
