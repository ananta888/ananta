#!/usr/bin/env python3
"""Run a deterministic, non-executing CodeCompass prompt matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark.ornith_benchmark_support import (  # noqa: E402
    OrnithBenchmarkError,
    call_openai_chat,
    evidence_projection,
    load_hub_assignment,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    matrix_bytes = args.matrix.read_bytes()
    matrix = json.loads(matrix_bytes)
    repeats = int(matrix.get("repeats", 0))
    cases = matrix.get("cases")
    if (
        matrix.get("schema") != "ananta.ornith-codecompass-matrix.v1"
        or repeats < 5
        or not isinstance(cases, list)
        or not cases
    ):
        raise SystemExit("ornith_benchmark_matrix_invalid")
    assignment = load_hub_assignment()
    results = []
    try:
        for case in cases:
            prompt = str(case["prompt"])
            for repeat in range(repeats):
                observation = call_openai_chat(
                    args.endpoint, model=args.model, prompt=prompt, timeout_seconds=args.timeout_seconds
                )
                results.append({"case_id": case["case_id"], "repeat": repeat + 1, **observation})
    except (KeyError, OrnithBenchmarkError) as exc:
        write_report(
            args.output,
            {
                "schema": "ananta.ornith-codecompass-result.v1",
                "status": "failed",
                "reason_code": str(exc),
                "evidence": evidence_projection(assignment),
                "results": results,
            },
        )
        return 2
    write_report(
        args.output,
        {
            "schema": "ananta.ornith-codecompass-result.v1",
            "status": "passed",
            "matrix_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
            "evidence": evidence_projection(assignment),
            "results": results,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
