"""Standardized specialist benchmark metrics (GBMF-007).

Produces one versioned, machine-readable result per (specialist, model
candidate, benchmark version): accuracy, macro-F1, per-class precision/recall,
confusion matrix, *separately reported* false-allow / false-deny rates for
gate-type decisions, resource usage and baseline deltas. Safety-relevant error
types are never hidden inside a single accuracy number.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.specialist_decision import (
    SAFETY_ALLOW,
    SAFETY_DENY,
    SpecialistDecisionContract,
    canonical_digest,
)

BENCHMARK_RESULT_SCHEMA = "ananta.specialist-benchmark-result.v1"
BASELINE_KINDS = frozenset({"untrained_base", "prompt_gate_stack", "previous_specialist"})


class SpecialistBenchmarkError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class Prediction:
    """One holdout case: expected label vs. the parsed model decision."""

    example_id: str
    expected: str
    predicted: str | None  # None = unparseable / schema error
    confidence: float | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None


@dataclass(frozen=True)
class ResourceUsage:
    """Training budget and hardware footprint stored with the metrics."""

    train_seconds: float = 0.0
    train_steps: int = 0
    peak_vram_bytes: int = 0
    peak_ram_bytes: int = 0
    parameter_count: int = 0
    adapter_bytes: int = 0

    def to_mapping(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _finite(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def classification_metrics(contract: SpecialistDecisionContract, predictions: Sequence[Prediction]) -> dict[str, Any]:
    labels = list(contract.allowed_labels)
    if not predictions:
        raise SpecialistBenchmarkError("specialist_benchmark_empty")
    confusion = {expected: {predicted: 0 for predicted in [*labels, "invalid"]} for expected in labels}
    correct = invalid = 0
    for item in predictions:
        if item.expected not in labels:
            raise SpecialistBenchmarkError("specialist_benchmark_label_invalid")
        predicted = item.predicted if item.predicted in labels else "invalid"
        confusion[item.expected][predicted] += 1
        if predicted == "invalid":
            invalid += 1
        elif predicted == item.expected:
            correct += 1
    per_class = {}
    f1_values = []
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(count for predicted, count in confusion[label].items() if predicted != label)
        precision, recall = _rate(tp, tp + fp), _rate(tp, tp + fn)
        f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
        support = sum(confusion[label].values())
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": support}
        if support:
            f1_values.append(f1 or 0.0)
    return {
        "cases": len(predictions),
        "accuracy": correct / len(predictions),
        "macro_f1": sum(f1_values) / len(f1_values) if f1_values else 0.0,
        "invalid_output_rate": invalid / len(predictions),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def safety_metrics(contract: SpecialistDecisionContract, predictions: Sequence[Prediction]) -> dict[str, Any]:
    """False-allow / false-deny reported separately for gate-type contracts."""
    allow = {label for label in contract.allowed_labels if contract.safety_role(label) == SAFETY_ALLOW}
    deny = {label for label in contract.allowed_labels if contract.safety_role(label) == SAFETY_DENY}
    if not allow and not deny:
        return {"gate_type": False}
    false_allow = sum(1 for p in predictions if p.predicted in allow and p.expected not in allow)
    should_deny = sum(1 for p in predictions if p.expected in deny)
    false_deny = sum(1 for p in predictions if p.predicted in deny and p.expected not in deny)
    should_allow = sum(1 for p in predictions if p.expected in allow)
    deferred = sum(1 for p in predictions if p.predicted in contract.deferral_labels)
    return {
        "gate_type": True,
        "false_allow": false_allow,
        "false_allow_rate": _rate(false_allow, len(predictions) - should_allow),
        "false_deny": false_deny,
        "false_deny_rate": _rate(false_deny, len(predictions) - should_deny),
        "deferral_rate": deferred / len(predictions),
    }


def resource_metrics(predictions: Sequence[Prediction], usage: ResourceUsage) -> dict[str, Any]:
    latencies = sorted(p.latency_ms for p in predictions if p.latency_ms is not None)
    tokens = [p.input_tokens for p in predictions if p.input_tokens is not None]
    return {
        "latency_ms_p50": _finite(latencies[len(latencies) // 2]) if latencies else None,
        "latency_ms_p95": _finite(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]) if latencies else None,
        "time_to_decision_ms_mean": _finite(sum(latencies) / len(latencies)) if latencies else None,
        "input_tokens_mean": _finite(sum(tokens) / len(tokens)) if tokens else None,
        **usage.to_mapping(),
    }


@dataclass(frozen=True)
class BenchmarkResult:
    specialist_id: str
    contract_version: str
    benchmark_version: str
    holdout_digest: str
    candidate_id: str
    base_model: str
    method: str
    classification: Mapping[str, Any]
    safety: Mapping[str, Any]
    resources: Mapping[str, Any]
    calibration: Mapping[str, Any] | None = None
    baselines: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        body = {
            "schema": BENCHMARK_RESULT_SCHEMA,
            "specialist_id": self.specialist_id,
            "contract_version": self.contract_version,
            "benchmark_version": self.benchmark_version,
            "holdout_digest": self.holdout_digest,
            "candidate_id": self.candidate_id,
            "base_model": self.base_model,
            "method": self.method,
            "classification": dict(self.classification),
            "safety": dict(self.safety),
            "resources": dict(self.resources),
            "calibration": dict(self.calibration) if self.calibration is not None else None,
            "baselines": {name: dict(value) for name, value in self.baselines.items()},
        }
        return {**body, "result_digest": canonical_digest(body)}

    def metric(self, path: str) -> float | None:
        section, _, name = path.partition(".")
        value = getattr(self, section, None)
        if not isinstance(value, Mapping):
            return None
        raw = value.get(name)
        return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


def benchmark(
    contract: SpecialistDecisionContract,
    predictions: Sequence[Prediction],
    *,
    benchmark_version: str,
    holdout_digest: str,
    candidate_id: str,
    base_model: str,
    method: str,
    usage: ResourceUsage | None = None,
    calibration: Mapping[str, Any] | None = None,
    baselines: Mapping[str, "BenchmarkResult"] | None = None,
) -> BenchmarkResult:
    """Evaluate one candidate on the frozen holdout and attach baseline deltas."""
    usage = usage if usage is not None else ResourceUsage()
    result = BenchmarkResult(
        specialist_id=contract.specialist_id,
        contract_version=contract.contract_version,
        benchmark_version=benchmark_version,
        holdout_digest=holdout_digest,
        candidate_id=candidate_id,
        base_model=base_model,
        method=method,
        classification=classification_metrics(contract, predictions),
        safety=safety_metrics(contract, predictions),
        resources=resource_metrics(predictions, usage),
        calibration=calibration,
    )
    deltas: dict[str, dict[str, Any]] = {}
    for kind, baseline in dict(baselines or {}).items():
        if kind not in BASELINE_KINDS:
            raise SpecialistBenchmarkError("specialist_benchmark_baseline_kind_invalid")
        if baseline.holdout_digest != holdout_digest or baseline.benchmark_version != benchmark_version:
            raise SpecialistBenchmarkError("specialist_benchmark_baseline_mismatch")
        deltas[kind] = {
            "candidate_id": baseline.candidate_id,
            "accuracy_delta": result.classification["accuracy"] - baseline.classification["accuracy"],
            "macro_f1_delta": result.classification["macro_f1"] - baseline.classification["macro_f1"],
            "false_allow_rate_delta": _delta(result.safety.get("false_allow_rate"), baseline.safety.get("false_allow_rate")),
            "false_deny_rate_delta": _delta(result.safety.get("false_deny_rate"), baseline.safety.get("false_deny_rate")),
            "latency_ms_p50_delta": _delta(result.resources.get("latency_ms_p50"), baseline.resources.get("latency_ms_p50")),
            "adapter_bytes_delta": _delta(result.resources.get("adapter_bytes"), baseline.resources.get("adapter_bytes")),
        }
    return BenchmarkResult(**{**result.__dict__, "baselines": deltas})


def _delta(candidate: Any, baseline: Any) -> float | None:
    if isinstance(candidate, (int, float)) and isinstance(baseline, (int, float)):
        return float(candidate) - float(baseline)
    return None


def predictions_from_outputs(
    contract: SpecialistDecisionContract,
    cases: Iterable[tuple[str, str, Any]],
    *,
    latencies_ms: Mapping[str, float] | None = None,
) -> list[Prediction]:
    """Parse raw model outputs for (example_id, expected_label, raw_output) triples."""
    latencies_ms = dict(latencies_ms or {})
    result = []
    for example_id, expected, raw in cases:
        try:
            parsed = contract.parse_output(raw)
            predicted, confidence = parsed["label"], parsed["confidence"]
        except Exception:  # SpecialistContractError or TypeError: schema/parse failure counts as invalid
            predicted, confidence = None, None
        result.append(Prediction(example_id, expected, predicted, confidence, latencies_ms.get(example_id)))
    return result
