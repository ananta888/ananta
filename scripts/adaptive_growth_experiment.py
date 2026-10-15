#!/usr/bin/env python3
"""CPU/CUDA reference observation; productive runs use Hub research assignments."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ananta_contracts.adaptive_growth import GrowthBudget, GrowthModelConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 23, 47])
    parser.add_argument("--max-training-tokens", type=int, default=65536)
    parser.add_argument("--max-training-flops", type=int, default=5_000_000_000)
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument(
        "--dataset", type=Path, help="Frozen JSON train/validation/holdout splits, each with de/en lists"
    )
    parser.add_argument(
        "--dataset-license", action="append", default=[], help="Declared license; repeat for mixed sources"
    )
    args = parser.parse_args()
    config = GrowthModelConfig()
    budget = GrowthBudget(
        max_training_tokens=args.max_training_tokens,
        max_training_flops=args.max_training_flops,
        max_seconds=args.max_seconds,
    )
    if args.dataset is None and args.dataset_license:
        parser.error("--dataset-license requires --dataset")
    if args.dataset is not None and not args.dataset_license:
        parser.error("--dataset requires an explicit --dataset-license declaration")
    from worker.training.growth.experiment import reference_splits, run_experiment, validate_splits

    splits = reference_splits()
    if args.dataset is not None:
        try:
            with args.dataset.open("rb") as stream:
                payload = stream.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                parser.error("growth_dataset_file_budget_exceeded")
            splits = json.loads(payload)
            validate_splits(splits)
        except (OSError, ValueError):
            parser.error("growth_dataset_file_invalid")
    if args.dry_run:
        print(
            json.dumps(
                {
                    "model": config.to_dict(),
                    "device": args.device,
                    "execution": "not_run",
                    "production_release_evidence": False,
                    "dataset": "provided_split_corpus" if args.dataset else "original_synthetic_reference",
                }
            )
        )
        return 0
    if args.output is None:
        parser.error("--output is required for the reference observation")
    checkpoint_sink = None
    if args.checkpoint_dir is not None:
        from worker.training.research.checkpoint import ResearchCheckpointManager

        manager = ResearchCheckpointManager(args.checkpoint_dir, max_checkpoint_bytes=16 * 1024 * 1024)

        def checkpoint_sink(content, step):
            return manager.publish(
                stage_id="reference-growth", attempt_id="local-observation", optimizer_step=step, content=content
            )

    try:
        # Check the output directory before training and publish atomically.
        with tempfile.TemporaryDirectory(dir=args.output.parent, prefix=".growth-observation-") as directory:
            report = run_experiment(
                splits=splits,
                config=config,
                budget=budget,
                steps=args.steps,
                seeds=tuple(args.seeds),
                device=args.device,
                checkpoint_sink=checkpoint_sink,
                dataset_license_ids=tuple(args.dataset_license),
            )
            temporary = Path(directory) / "report.json"
            temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
            temporary.replace(args.output)
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
        return 1
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
