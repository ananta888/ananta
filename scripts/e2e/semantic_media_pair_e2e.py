#!/usr/bin/env python3
"""Run or explicitly mark unavailable the two-browser pair E2E gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.services.semantic_media_program_evidence import write_report

try:
    from scripts.e2e.semantic_media_e2e_report import ROOT, run_playwright_gate
except ModuleNotFoundError:  # Direct execution sets scripts/e2e as sys.path[0].
    from semantic_media_e2e_report import ROOT, run_playwright_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/e2e/semantic-media-pair-report.json")
    args = parser.parse_args()
    evidence = run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=args.execute_live,
    )
    write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
