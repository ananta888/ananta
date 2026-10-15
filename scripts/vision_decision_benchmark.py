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
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--doclaynet-dir", type=Path)
    args = parser.parse_args()
    config = VisionDecisionConfig(
        enabled=True, url=args.url, models=(args.model,), require_cache_isolation=args.require_cache_isolation
    )
    config.validate()
    options = {}
    if args.doclaynet_dir:
        from agent.services.public_decision_datasets import load_doclaynet

        dataset = load_doclaynet(args.doclaynet_dir)
        options = {"dataset": dataset, "max_cases": sum(c.split != "train" for c in dataset.cases)}
    report = run_vision_pilot(VisionDecisionProvider(config), max_seconds=args.max_seconds, **options)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"cases": report["cases"], "report": str(args.output), "production_release_evidence": False}))
    return 0 if report["evaluation_validity"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
