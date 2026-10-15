"""One delegated experiment, with fixed comparison arms and no release authority."""

from __future__ import annotations

import hashlib
import math
import resource
import statistics
from dataclasses import asdict, replace

from ananta_contracts.adaptive_growth import GrowthBudget, GrowthModelConfig, estimate_parameters
from worker.training.growth.checkpoint import serialize
from worker.training.growth.evaluation import score_probes
from worker.training.growth.model import build_model
from worker.training.growth.trainer import TorchGrowthTrainer, estimated_step_flops, evaluate
from worker.training.growth.transforms import grow_depth, grow_width, migrate_adam, upcycle
from worker.training.research.modeling import require_torch


def validate_splits(splits: dict) -> None:
    if not isinstance(splits, dict) or set(splits) != {"train", "validation", "holdout"}:
        raise ValueError("growth_dataset_splits_invalid")
    seen = set()
    for split, languages in splits.items():
        if not isinstance(languages, dict) or set(languages) != {"de", "en"}:
            raise ValueError("growth_dataset_languages_invalid")
        for texts in languages.values():
            if (
                not isinstance(texts, list)
                or not texts
                or len(texts) > 256
                or any(not isinstance(text, str) or not 40 <= len(text) <= 8000 for text in texts)
            ):
                raise ValueError("growth_dataset_text_invalid")
            for text in texts:
                normalized = " ".join(text.casefold().split())
                digest = hashlib.sha256(normalized.encode()).hexdigest()
                if digest in seen:
                    raise ValueError("growth_dataset_contamination")
                seen.add(digest)


def reference_splits() -> dict:
    # Small original synthetic corpus; no model labels, external data or real
    # personal information. This measures execution/retention, not competence.
    return {
        "train": {
            "de": ["Die Zentrale verteilt Aufgaben. Ein Arbeiter bearbeitet nur seinen eigenen Auftrag."],
            "en": ["The control plane delegates tasks. Each worker executes its assigned operation only."],
        },
        "validation": {
            "de": ["Ein Ergebnis ist ein Vorschlag. Die Richtlinie entscheidet über die erlaubte Aktion."],
            "en": ["A model returns a proposal. Policy decides whether the requested action is permitted."],
        },
        "holdout": {
            "de": ["Unbekannte Quellen sind nicht belegt. Ein Test ersetzt keinen produktiven Nachweis."],
            "en": ["Unknown sources are unverified. A synthetic test cannot replace a production evidence record."],
        },
    }


def _tokens(languages: dict) -> tuple[int, ...]:
    return tuple("\n".join(text for texts in languages.values() for text in texts).encode())


def run_experiment(
    *,
    splits: dict,
    config: GrowthModelConfig = GrowthModelConfig(),
    budget: GrowthBudget = GrowthBudget(),
    seeds: tuple[int, ...] = (11, 23, 47),
    steps: int = 8,
    device: str = "cpu",
    checkpoint_sink=None,
    dataset_license_ids: tuple[str, ...] = (),
) -> dict:
    validate_splits(splits)
    if (
        not isinstance(dataset_license_ids, tuple)
        or len(dataset_license_ids) > 64
        or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in dataset_license_ids)
    ):
        raise ValueError("growth_dataset_license_invalid")
    reference_dataset = splits == reference_splits()
    licenses = sorted(set(dataset_license_ids or (("CC0-1.0",) if reference_dataset else ())))
    if (
        not 2 <= len(seeds) <= 8
        or len(set(seeds)) != len(seeds)
        or any(type(seed) is not int or not 0 <= seed <= 2**31 - 1 for seed in seeds)
        or type(steps) is not int
        or not 4 <= steps <= 512
        or device not in {"cpu", "cuda"}
    ):
        raise ValueError("growth_experiment_invalid")
    torch = require_torch()
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("growth_cuda_unavailable")
    if config.experts != 1:
        raise ValueError("growth_experiment_requires_dense")
    targets = (
        config,
        replace(config, depth=config.depth + 1),
        replace(config, width=config.width * 2, heads=config.heads * 2),
        replace(config, experts=4, top_k=2),
    )
    if any(estimate_parameters(target) > budget.max_parameters for target in targets):
        raise ValueError("growth_parameter_budget_exceeded_before_allocation")
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    trainer = TorchGrowthTrainer()
    train_tokens = _tokens(splits["train"])
    # One budget owns the ENTIRE experiment, including all repeated baselines.
    used_tokens, used_flops = 0, 0
    import time

    started = time.monotonic()
    rows = []
    transforms = {"depth": grow_depth, "width": grow_width, "moe": upcycle}
    for seed in seeds:
        for protocol in ("matched_tokens", "matched_estimated_flops"):
            for arm in ("fixed", "depth", "width", "moe"):
                for origin in ("fixed",) if arm == "fixed" else ("grown", "scratch"):
                    torch.manual_seed(seed)
                    base = build_model(config).to(device)
                    optimizer = torch.optim.AdamW(base.parameters(), lr=0.002)
                    local = []

                    def train(model, opt, count, offset=0):
                        nonlocal used_tokens, used_flops
                        remaining = replace(
                            budget,
                            max_training_tokens=budget.max_training_tokens - used_tokens,
                            max_training_flops=budget.max_training_flops - used_flops,
                            max_seconds=max(1, int(budget.max_seconds - (time.monotonic() - started))),
                        )
                        if time.monotonic() - started >= budget.max_seconds:
                            raise ValueError("growth_experiment_deadline_exceeded")
                        metrics = trainer.train(
                            model, opt, tokens=train_tokens, steps=count, budget=remaining, start_step=offset
                        )
                        used_tokens += metrics["tokens"]
                        used_flops += metrics["estimated_training_flops"]
                        if metrics["steps"] != count:
                            raise ValueError("growth_experiment_budget_insufficient")
                        local.append(metrics)

                    perturbation = None
                    parent_digest = None
                    parent_receipt = None
                    parent_holdout = None
                    if arm != "fixed" and origin == "grown":
                        train(base, optimizer, steps // 2)
                        before = evaluate(base, _tokens(splits["validation"]))
                        parent_holdout = {
                            language: evaluate(base, tuple("\n".join(texts).encode()))
                            for language, texts in splits["holdout"].items()
                        }
                        parent = serialize(base, optimizer, steps=steps // 2)
                        parent_digest = hashlib.sha256(parent).hexdigest()
                        if checkpoint_sink is not None:
                            parent_receipt = checkpoint_sink(parent, steps // 2)
                        model = transforms[arm](base)
                        optimizer = migrate_adam(optimizer, base, model, operation="upcycle" if arm == "moe" else arm)
                        perturbation = evaluate(model, _tokens(splits["validation"])) - before
                        remaining_steps = steps - steps // 2
                        offset = steps // 2
                    else:
                        model = base if arm == "fixed" else transforms[arm](base)
                        if origin == "scratch":
                            model = build_model(model.config).to(device)
                        optimizer = torch.optim.AdamW(model.parameters(), lr=0.002)
                        remaining_steps, offset = steps, 0
                    if protocol == "matched_estimated_flops":
                        # Same ceiling for every arm; integer steps leave at
                        # most one step of unused compute. Tokens are reported.
                        ceiling = (
                            estimated_step_flops(
                                build_model(replace(config, width=config.width * 2, heads=config.heads * 2)),
                                config.context_length,
                            )
                            * steps
                        )
                        consumed = sum(m["estimated_training_flops"] for m in local)
                        remaining_steps = max(
                            1, (ceiling - consumed) // estimated_step_flops(model, config.context_length)
                        )
                    train(model, optimizer, remaining_steps, offset)
                    losses = {
                        split: {
                            language: evaluate(model, tuple("\n".join(texts).encode()))
                            for language, texts in languages.items()
                        }
                        for split, languages in splits.items()
                        if split != "train"
                    }
                    checkpoint = serialize(
                        model, optimizer, steps=offset + remaining_steps, parent_digest=parent_digest
                    )
                    probes = score_probes(model)
                    checkpoint_receipt = (
                        checkpoint_sink(checkpoint, offset + remaining_steps) if checkpoint_sink is not None else None
                    )
                    rows.append(
                        {
                            "seed": seed,
                            "protocol": protocol,
                            "arm": arm,
                            "origin": origin,
                            "config": model.config.to_dict(),
                            "immediate_validation_loss_delta": perturbation,
                            "metrics": local[-1],
                            "training_tokens": sum(m["tokens"] for m in local),
                            "estimated_training_flops": sum(m["estimated_training_flops"] for m in local),
                            "loss": losses,
                            "checkpoint_sha256": hashlib.sha256(checkpoint).hexdigest(),
                            "parent_checkpoint_sha256": parent_digest,
                            "parent_checkpoint_receipt": parent_receipt,
                            "checkpoint_receipt": checkpoint_receipt,
                            "heldout_probes": probes,
                            "retention_loss_delta": {
                                language: losses["holdout"][language] - loss
                                for language, loss in parent_holdout.items()
                            }
                            if parent_holdout
                            else None,
                        }
                    )
    comparisons = []
    for protocol in ("matched_tokens", "matched_estimated_flops"):
        for arm in transforms:
            differences = []
            for seed in seeds:
                pair = [r for r in rows if r["protocol"] == protocol and r["arm"] == arm and r["seed"] == seed]
                grown = next(r for r in pair if r["origin"] == "grown")
                scratch = next(r for r in pair if r["origin"] == "scratch")
                differences.append(
                    statistics.fmean(grown["loss"]["holdout"].values())
                    - statistics.fmean(scratch["loss"]["holdout"].values())
                )
            mean = statistics.fmean(differences)
            # Conservative Student-t critical values for the supported 2..8 seeds.
            critical = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365}[len(seeds)]
            margin = critical * statistics.stdev(differences) / math.sqrt(len(seeds))
            comparisons.append(
                {
                    "protocol": protocol,
                    "arm": arm,
                    "seeds": len(seeds),
                    "mean_holdout_loss_delta": mean,
                    "ci95": [mean - margin, mean + margin],
                    "benefit_demonstrated": mean + margin < -0.01,
                }
            )
    return {
        "schema": "ananta.adaptive-growth-report.v1",
        "classification": "synthetic_technical_observation" if reference_dataset else "technical_observation",
        "production_release_evidence": False,
        "dataset_license": ", ".join(licenses) if licenses else None,
        "dataset_license_ids": licenses,
        "dataset_kind": "original_synthetic_reference" if reference_dataset else "provided_split_corpus",
        "dataset_digest": hashlib.sha256(__import__("json").dumps(splits, sort_keys=True).encode()).hexdigest(),
        "device": device,
        "torch_version": torch.__version__,
        "budget": asdict(budget),
        "consumed_tokens": used_tokens,
        "estimated_training_flops": used_flops,
        "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "rows": rows,
        "comparisons": comparisons,
        "decision": "experimental_only",
        "reason": "A tiny reference-model observation is insufficient for production promotion.",
    }
