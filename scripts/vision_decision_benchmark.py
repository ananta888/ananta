#!/usr/bin/env python3
"""Run the original frozen DE/EN vision pilot against an explicitly local server."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.services.vision_decision_benchmark import run_vision_pilot
from agent.services.vision_decision_provider import VisionDecisionConfig, VisionDecisionProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-cache-isolation", action="store_true")
    args = parser.parse_args()
    config = VisionDecisionConfig(
        enabled=True, url=args.url, models=(args.model,), require_cache_isolation=args.require_cache_isolation
    )
    config.validate()
    report = run_vision_pilot(VisionDecisionProvider(config))
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"cases": report["cases"], "report": str(args.output), "production_release_evidence": False}))


if __name__ == "__main__":
    main()
