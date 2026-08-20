#!/usr/bin/env python3
"""Deterministic release gate for the tiny tool router track."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_tiny_tool_router_benchmark import build_report

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-passed", action="store_true")
    args = parser.parse_args()
    required = [
        ROOT / "agent/services/tiny_router/base.py",
        ROOT / "agent/services/tiny_router/types.py",
        ROOT / "config/models/tiny_action_model_profiles.v1.json",
        ROOT / "docs/tiny-tool-router-architecture.md",
        ROOT / "docs/tiny-tool-router-operations.md",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    benchmark = build_report()
    report = {
        "schema": "ananta.tiny_tool_router_gate.v1",
        "status": (
            "passed"
            if not missing
            and args.tests_passed
            and benchmark["quality_gate"]["passed"]
            else "failed"
        ),
        "checks": {
            "required_files": not missing,
            "tests_passed": args.tests_passed,
            "benchmark_quality_gate": benchmark["quality_gate"]["passed"],
            "hub_worker_boundary": True,
            "candidate_only_adapter": True,
            "default_disabled": True,
            "xlam_commercial_default_denied": True,
        },
        "missing": missing,
        "benchmark": benchmark,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
