"""Deterministic Base-vs-LoRA-vs-dendritic comparison and leakage gates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.dendritic_memory_evaluation_attestation import DendriticMemoryEvaluationAttestation
from ananta_contracts.dendritic_memory import canonical_digest, require_digest


class DendriticMemoryEvaluationService:
    REQUIRED_METRICS = frozenset({"accuracy", "loss", "calibration_error", "latency_ms", "peak_memory_bytes"})
    REQUIRED_SPLITS = frozenset({"train", "validation", "transfer", "negative_control", "test"})
    REQUIRED_RESOURCES = frozenset(
        {"peak_vram_bytes", "host_ram_bytes", "duration_ms", "artifact_bytes", "tokens_per_second"}
    )

    def __init__(self, attestations: DendriticMemoryEvaluationAttestation) -> None:
        self._attestations = attestations

    def compare(
        self,
        *,
        baseline: Mapping[str, Any],
        lora: Mapping[str, Any],
        dendritic: Mapping[str, Any],
        leakage: Mapping[str, Any],
    ) -> dict[str, Any]:
        runs = (baseline, lora, dendritic)
        reasons: list[str] = []
        comparable_fields = ("dataset_manifest_digest", "test_split_digest", "hardware_digest", "task_family")
        if any(any(run.get(field) != baseline.get(field) for field in comparable_fields) for run in runs[1:]):
            reasons.append("dendritic_evaluation_not_comparable")
        split_sets = [self._splits(run.get("split_digests")) for run in runs]
        if any(value != split_sets[0] for value in split_sets[1:]):
            reasons.append("dendritic_evaluation_splits_not_comparable")
        seeds = tuple(dendritic.get("seeds") or ())
        if len(seeds) < 3 or len(seeds) != len(set(seeds)) or any(not isinstance(seed, int) for seed in seeds):
            reasons.append("dendritic_evaluation_seeds_insufficient")
        metrics = [self._metrics(run.get("metrics")) for run in runs]
        resources = [self._resources(run.get("resources")) for run in runs]
        provenance = [self._provenance(run.get("provenance")) for run in runs]
        parameter_counts = [int(run.get("trainable_parameter_count") or 0) for run in runs]
        if parameter_counts[1] <= 0 or parameter_counts[2] <= 0:
            reasons.append("dendritic_parameter_budget_missing")
        elif abs(parameter_counts[1] - parameter_counts[2]) / max(parameter_counts[1], parameter_counts[2]) > 0.05:
            reasons.append("dendritic_parameter_budget_not_matched")
        leakage_passed = self._leakage(leakage)
        if not leakage_passed:
            reasons.append("dendritic_leakage_gate_failed")
        if metrics[2]["accuracy"] < metrics[0]["accuracy"] or metrics[2]["loss"] > metrics[0]["loss"]:
            reasons.append("dendritic_baseline_regression")
        result = {
            "schema": "ananta.dendritic-memory-evaluation.v1",
            "comparable": "dendritic_evaluation_not_comparable" not in reasons,
            "experiment_eligible": not reasons,
            "production_eligible": False,
            "claims_verified": False,
            "reason_codes": reasons,
            "dataset_manifest_digest": require_digest(
                baseline.get("dataset_manifest_digest"), "dataset_manifest_digest"
            ),
            "dendritic_pack_digest": require_digest(dendritic.get("pack_digest"), "dendritic_pack_digest"),
            "base_input_digest": canonical_digest(baseline),
            "lora_input_digest": canonical_digest(lora),
            "dendritic_input_digest": canonical_digest(dendritic),
            "leakage_input_digest": canonical_digest(leakage),
            "metrics": {"base": metrics[0], "lora": metrics[1], "dendritic": metrics[2]},
            "resources": {
                "base": resources[0],
                "lora": resources[1],
                "dendritic": resources[2],
            },
            "provenance": {
                "base": provenance[0],
                "lora": provenance[1],
                "dendritic": provenance[2],
            },
            "split_digests": split_sets[0],
            "metric_definitions": {
                "accuracy": {"direction": "higher_is_better", "range": [0, 1]},
                "loss": {"direction": "lower_is_better", "range": [0, None]},
                "calibration_error": {"direction": "lower_is_better", "range": [0, 1]},
                "latency_ms": {"direction": "lower_is_better", "range": [0, None]},
                "peak_memory_bytes": {"direction": "lower_is_better", "range": [0, None]},
            },
            "benchmark_groups": ["trained", "transfer", "negative_control", "leakage"],
            "seeds": list(seeds),
            "seed_aggregation": "multiple_seeds",
            "human_intervention_required": False,
        }
        result["evaluation_digest"] = canonical_digest(result)
        result["attestation"] = self._attestations.issue(result)
        return result

    def continual_learning(self, *, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not 3 <= len(runs) <= 128:
            raise ValueError("dendritic_continual_runs_invalid")
        required = {
            "pack_order",
            "mode",
            "forward_transfer",
            "backward_transfer",
            "forgetting",
            "interference",
            "seed",
            "base_model_before_digest",
            "base_model_after_digest",
        }
        if any(set(run) != required for run in runs):
            raise ValueError("dendritic_continual_run_fields_invalid")
        normalized: list[dict[str, Any]] = []
        for run in runs:
            values = {
                key: float(run[key])
                for key in {"forward_transfer", "backward_transfer", "forgetting", "interference"}
            }
            if any(not math.isfinite(value) for value in values.values()):
                raise ValueError("dendritic_continual_metric_invalid")
            order = tuple(run["pack_order"])
            mode = str(run["mode"])
            if (
                not 0 <= len(order) <= 16
                or len(order) != len(set(order))
                or any(require_digest(value, "continual_pack_digest") != value for value in order)
                or mode not in {"disabled", "single", "multiple"}
                or (mode == "disabled" and order)
                or (mode == "single" and len(order) != 1)
                or (mode == "multiple" and len(order) < 2)
                or require_digest(run["base_model_before_digest"], "base_model_before_digest")
                != require_digest(run["base_model_after_digest"], "base_model_after_digest")
            ):
                raise ValueError("dendritic_continual_binding_invalid")
            normalized.append({**dict(run), "pack_order": list(order), **values})
        if {run["mode"] for run in normalized} != {"disabled", "single", "multiple"}:
            raise ValueError("dendritic_continual_modes_missing")
        if len({run["seed"] for run in normalized}) < 3:
            raise ValueError("dendritic_continual_seeds_insufficient")
        return {
            "schema": "ananta.dendritic-memory-continual-report.v1",
            "runs": normalized,
            "report_digest": canonical_digest(normalized),
            "seed_count": len({run["seed"] for run in normalized}),
            "pack_orders": [run["pack_order"] for run in normalized],
            "claims_verified": False,
        }

    def _metrics(self, raw: object) -> dict[str, float]:
        if not isinstance(raw, Mapping) or set(raw) != self.REQUIRED_METRICS:
            raise ValueError("dendritic_metric_set_invalid")
        values = {key: float(raw[key]) for key in self.REQUIRED_METRICS}
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("dendritic_metric_value_invalid")
        if values["accuracy"] > 1 or values["calibration_error"] > 1:
            raise ValueError("dendritic_metric_value_invalid")
        return values

    def _splits(self, raw: object) -> dict[str, str]:
        if not isinstance(raw, Mapping) or set(raw) != self.REQUIRED_SPLITS:
            raise ValueError("dendritic_evaluation_split_fields_invalid")
        return {key: require_digest(raw[key], f"{key}_split_digest") for key in sorted(raw)}

    def _resources(self, raw: object) -> dict[str, float]:
        if not isinstance(raw, Mapping) or set(raw) != self.REQUIRED_RESOURCES:
            raise ValueError("dendritic_resource_fields_invalid")
        values = {key: float(raw[key]) for key in self.REQUIRED_RESOURCES}
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("dendritic_resource_value_invalid")
        return values

    @staticmethod
    def _provenance(raw: object) -> dict[str, Any]:
        required = {
            "repository_revision",
            "worker_image_digest",
            "dependency_lock_digest",
            "runtime_versions",
            "deterministic",
        }
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("dendritic_provenance_fields_invalid")
        revision = str(raw["repository_revision"] or "").strip()
        versions = raw["runtime_versions"]
        if (
            not 7 <= len(revision) <= 64
            or not isinstance(versions, Mapping)
            or not 1 <= len(versions) <= 16
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in versions.items())
            or not isinstance(raw["deterministic"], bool)
        ):
            raise ValueError("dendritic_provenance_value_invalid")
        return {
            "repository_revision": revision,
            "worker_image_digest": require_digest(raw["worker_image_digest"], "worker_image_digest"),
            "dependency_lock_digest": require_digest(
                raw["dependency_lock_digest"], "dependency_lock_digest"
            ),
            "runtime_versions": dict(versions),
            "deterministic": raw["deterministic"],
        }

    @staticmethod
    def _leakage(raw: Mapping[str, Any]) -> bool:
        expected = {
            "exact_duplicates",
            "normalized_duplicates",
            "canary_secret_reconstructions",
            "untrained_control_reconstructions",
            "paraphrase_overlap_passed",
            "ood_passed",
        }
        if set(raw) != expected:
            raise ValueError("dendritic_leakage_fields_invalid")
        count_fields = (
            "exact_duplicates",
            "normalized_duplicates",
            "canary_secret_reconstructions",
            "untrained_control_reconstructions",
        )
        counts = tuple(raw[key] for key in count_fields)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
            raise ValueError("dendritic_leakage_count_invalid")
        return not any(counts) and raw["paraphrase_overlap_passed"] is True and raw["ood_passed"] is True


__all__ = ["DendriticMemoryEvaluationService"]
