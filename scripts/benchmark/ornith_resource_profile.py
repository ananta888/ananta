#!/usr/bin/env python3
"""Measure a bounded local Ornith runtime without minting evidence IDs."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark.ornith_benchmark_support import (  # noqa: E402
    OrnithBenchmarkError,
    call_openai_chat,
    enforce_resource_safety,
    evidence_projection,
    load_hub_assignment,
    resource_dict,
    sample_resources,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 7200 or not 1 <= args.interval_seconds <= 300:
        raise SystemExit("ornith_profile_limits_invalid")
    assignment = load_hub_assignment()
    baseline = sample_resources()
    samples = [resource_dict(baseline)]
    probes = []
    deadline = time.monotonic() + args.duration_seconds
    try:
        while True:
            probes.append(
                call_openai_chat(
                    args.endpoint,
                    model=args.model,
                    prompt='Return exactly the JSON object {"status":"ok"}.',
                    timeout_seconds=args.timeout_seconds,
                )
            )
            current = sample_resources()
            enforce_resource_safety(baseline, current)
            samples.append(resource_dict(current))
            if time.monotonic() >= deadline:
                break
            time.sleep(min(args.interval_seconds, max(0, deadline - time.monotonic())))
    except OrnithBenchmarkError as exc:
        write_report(
            args.output,
            {
                "schema": "ananta.ornith-resource-result.v1",
                "status": "failed",
                "reason_code": str(exc),
                "evidence": evidence_projection(assignment),
                "samples": samples,
                "probes": probes,
            },
        )
        return 2
    write_report(
        args.output,
        {
            "schema": "ananta.ornith-resource-result.v1",
            "status": "passed",
            "evidence": evidence_projection(assignment),
            "samples": samples,
            "probes": probes,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
