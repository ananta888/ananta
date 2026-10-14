#!/usr/bin/env python3
"""CPU/CUDA reference observation; productive runs use Hub research assignments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ananta_contracts.adaptive_growth import GrowthBudget, GrowthModelConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--checkpoint-dir", type=Path)
    args = parser.parse_args()
    config, budget = GrowthModelConfig(), GrowthBudget()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "model": config.to_dict(),
                    "device": args.device,
                    "execution": "not_run",
                    "production_release_evidence": False,
                }
            )
        )
        return 0
    if args.output is None:
        parser.error("--output is required for the reference observation")
    from worker.training.growth.experiment import reference_splits, run_experiment

    checkpoint_sink = None
    if args.checkpoint_dir is not None:
        from worker.training.research.checkpoint import ResearchCheckpointManager

        manager = ResearchCheckpointManager(args.checkpoint_dir, max_checkpoint_bytes=16 * 1024 * 1024)

        def checkpoint_sink(content, step):
            return manager.publish(
                stage_id="reference-growth", attempt_id="local-observation", optimizer_step=step, content=content
            )

    try:
        report = run_experiment(
            splits=reference_splits(),
            config=config,
            budget=budget,
            steps=args.steps,
            device=args.device,
            checkpoint_sink=checkpoint_sink,
        )
    except (ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
        return 1
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "status": "completed",
                "report": str(args.output),
                "rows": len(report["rows"]),
                "decision": report["decision"],
                "production_release_evidence": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
