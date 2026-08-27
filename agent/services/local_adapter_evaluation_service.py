"""Independent deterministic evaluation for local Needle/LFM candidates."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_REQUIRED_SLICES = frozenset({"golden", "ood", "abstain", "injection", "malformed_schema"})


@dataclass(frozen=True, slots=True)
class ToolEvaluationCase:
    case_id: str
    slice_id: str
    allowed_tools: frozenset[str]
    expected_tool: str | None
    required_arguments: Mapping[str, type]
    expected_arguments: Mapping[str, Any]
    base_decision: Any
    candidate_decisions: tuple[Any, ...]
    candidate_confidence: float | None
    latency_ms: float
    memory_bytes: int
    holdout: bool = True


@dataclass(frozen=True, slots=True)
class LocalAdapterEvaluationReport:
    report_sha256: str
    dataset_sha256: str
    candidate_sha256: str
    golden_set_sha256: str
    policy_sha256: str
    case_count: int
    json_validity: float
    known_tool_rate: float
    required_fields_rate: float
    argument_type_rate: float
    known_arguments_rate: float
    selection_accuracy: float
    baseline_selection_accuracy: float
    argument_match: float
    baseline_argument_match: float
    deterministic: bool
    confidence_calibrated: bool
    confidence_brier_score: float
    confidence_max_brier_score: float
    latency_p95_ms: float
    memory_peak_bytes: int
    slice_accuracy: Mapping[str, float]
    baseline_slice_accuracy: Mapping[str, float]
    slice_regressions: Mapping[str, float]
    passed_required_slices: bool
    evaluation_seed: int

    def __post_init__(self) -> None:
        for value in (
            self.report_sha256,
            self.dataset_sha256,
            self.candidate_sha256,
            self.golden_set_sha256,
            self.policy_sha256,
        ):
            _digest(value)
        if isinstance(self.case_count, bool) or not isinstance(self.case_count, int) or self.case_count <= 0:
            raise ValueError("local_adapter_evaluation_case_count_invalid")
        if (
            isinstance(self.evaluation_seed, bool)
            or not isinstance(self.evaluation_seed, int)
            or not 0 <= self.evaluation_seed <= 2**32 - 1
        ):
            raise ValueError("local_adapter_evaluation_seed_invalid")
        for name in (
            "json_validity",
            "known_tool_rate",
            "required_fields_rate",
            "argument_type_rate",
            "known_arguments_rate",
            "selection_accuracy",
            "baseline_selection_accuracy",
            "argument_match",
            "baseline_argument_match",
            "confidence_brier_score",
            "confidence_max_brier_score",
        ):
            _report_rate(getattr(self, name), name)
        if self.confidence_calibrated != (self.confidence_brier_score <= self.confidence_max_brier_score):
            raise ValueError("local_adapter_evaluation_confidence_inconsistent")
        if (
            isinstance(self.latency_p95_ms, bool)
            or not isinstance(self.latency_p95_ms, (int, float))
            or not math.isfinite(float(self.latency_p95_ms))
            or float(self.latency_p95_ms) < 0.0
        ):
            raise ValueError("local_adapter_evaluation_latency_invalid")
        if (
            isinstance(self.memory_peak_bytes, bool)
            or not isinstance(self.memory_peak_bytes, int)
            or self.memory_peak_bytes < 0
        ):
            raise ValueError("local_adapter_evaluation_memory_invalid")
        for name in ("slice_accuracy", "baseline_slice_accuracy", "slice_regressions"):
            values = getattr(self, name)
            if not values or any(not str(key).strip() for key in values):
                raise ValueError(f"local_adapter_evaluation_{name}_invalid")
            for value in values.values():
                _report_rate(value, name)
        keys = set(self.slice_accuracy)
        if keys != set(self.baseline_slice_accuracy) or keys != set(self.slice_regressions):
            raise ValueError("local_adapter_evaluation_slice_keys_mismatch")
        for slice_id in keys:
            expected_regression = max(
                0.0,
                float(self.baseline_slice_accuracy[slice_id]) - float(self.slice_accuracy[slice_id]),
            )
            if not math.isclose(float(self.slice_regressions[slice_id]), expected_regression, abs_tol=1e-12):
                raise ValueError("local_adapter_evaluation_slice_regression_inconsistent")
        expected_required = _REQUIRED_SLICES.issubset(keys) and all(
            self.slice_accuracy[slice_id] == 1.0 for slice_id in _REQUIRED_SLICES
        )
        if self.passed_required_slices != expected_required:
            raise ValueError("local_adapter_evaluation_required_slices_inconsistent")


class LocalAdapterEvaluationService:
    """Consumes fixed holdout outputs; candidates never evaluate themselves."""

    REQUIRED_SLICES = _REQUIRED_SLICES

    def __init__(self, *, maximum_confidence_brier_score: float = 0.05) -> None:
        if not 0.0 <= float(maximum_confidence_brier_score) <= 1.0:
            raise ValueError("local_adapter_confidence_threshold_invalid")
        self._maximum_confidence_brier_score = float(maximum_confidence_brier_score)

    def evaluate(
        self,
        cases: Sequence[ToolEvaluationCase],
        *,
        dataset_sha256: str,
        candidate_sha256: str,
        golden_set_sha256: str,
        policy_sha256: str,
        evaluation_seed: int,
    ) -> LocalAdapterEvaluationReport:
        if not cases or any(not case.holdout for case in cases):
            raise ValueError("local_adapter_evaluation_holdout_required")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("local_adapter_evaluation_case_duplicate")
        if any(not str(case.case_id).strip() or not str(case.slice_id).strip() for case in cases):
            raise ValueError("local_adapter_evaluation_case_invalid")
        if any(
            isinstance(case.latency_ms, bool)
            or not isinstance(case.latency_ms, (int, float))
            or not math.isfinite(float(case.latency_ms))
            or float(case.latency_ms) < 0.0
            or isinstance(case.memory_bytes, bool)
            or not isinstance(case.memory_bytes, int)
            or case.memory_bytes < 0
            for case in cases
        ):
            raise ValueError("local_adapter_evaluation_resource_measurement_invalid")
        if (
            isinstance(evaluation_seed, bool)
            or not isinstance(evaluation_seed, int)
            or not 0 <= evaluation_seed <= 2**32 - 1
        ):
            raise ValueError("local_adapter_evaluation_seed_invalid")
        bindings = {
            "dataset_sha256": _digest(dataset_sha256),
            "candidate_sha256": _digest(candidate_sha256),
            "golden_set_sha256": _digest(golden_set_sha256),
            "policy_sha256": _digest(policy_sha256),
        }
        rows = [self._score(case) for case in cases]
        slices: dict[str, list[bool]] = {}
        for case, row in zip(cases, rows, strict=True):
            slices.setdefault(case.slice_id, []).append(row["selection_match"] and row["arguments_match"])
        slice_accuracy = {key: sum(values) / len(values) for key, values in sorted(slices.items())}
        baseline_slices: dict[str, list[bool]] = {}
        for case, row in zip(cases, rows, strict=True):
            baseline_slices.setdefault(case.slice_id, []).append(
                row["baseline_selection_match"] and row["baseline_arguments_match"]
            )
        baseline_slice_accuracy = {key: sum(values) / len(values) for key, values in sorted(baseline_slices.items())}
        slice_regressions = {
            key: max(0.0, baseline_slice_accuracy[key] - slice_accuracy[key]) for key in slice_accuracy
        }
        latencies = sorted(max(0.0, float(case.latency_ms)) for case in cases)
        p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)
        canonical = {
            **bindings,
            "evaluation_seed": evaluation_seed,
            "cases": sorted(
                (
                    {
                        "case_id": case.case_id,
                        "slice_id": case.slice_id,
                        "allowed_tools": sorted(case.allowed_tools),
                        "expected_sha256": _value_digest(
                            {"tool": case.expected_tool, "arguments": case.expected_arguments}
                        ),
                        "baseline_sha256": _value_digest(case.base_decision),
                        "candidate_outputs_sha256": _value_digest(case.candidate_decisions),
                        "candidate_confidence": _finite_confidence(case.candidate_confidence),
                        "latency_ms": max(0.0, float(case.latency_ms)),
                        "memory_bytes": max(0, int(case.memory_bytes)),
                        "holdout": case.holdout,
                        "score": row,
                    }
                    for case, row in zip(cases, rows, strict=True)
                ),
                key=lambda item: str(item["case_id"]),
            ),
            "slice_accuracy": slice_accuracy,
            "baseline_slice_accuracy": baseline_slice_accuracy,
            "slice_regressions": slice_regressions,
        }
        confidence_values: list[float] = []
        confidence_values_valid = True
        for value in (case.candidate_confidence for case in cases):
            if (
                value is None
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                confidence_values_valid = False
                break
            confidence_values.append(float(value))
        confidence_brier_score = (
            sum(
                (float(confidence) - float(row["selection_match"] and row["arguments_match"])) ** 2
                for confidence, row in zip(confidence_values, rows, strict=True)
            )
            / len(rows)
            if confidence_values_valid
            else 1.0
        )
        confidence_calibrated = (
            confidence_values_valid and confidence_brier_score <= self._maximum_confidence_brier_score
        )
        canonical["confidence_brier_score"] = confidence_brier_score
        canonical["confidence_max_brier_score"] = self._maximum_confidence_brier_score
        return LocalAdapterEvaluationReport(
            report_sha256=hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest(),
            **bindings,
            case_count=len(cases),
            json_validity=_rate(rows, "json_valid"),
            known_tool_rate=_rate(rows, "known_tool"),
            required_fields_rate=_rate(rows, "required_fields"),
            argument_type_rate=_rate(rows, "argument_types"),
            known_arguments_rate=_rate(rows, "known_arguments"),
            selection_accuracy=_rate(rows, "selection_match"),
            baseline_selection_accuracy=_rate(rows, "baseline_selection_match"),
            argument_match=_rate(rows, "arguments_match"),
            baseline_argument_match=_rate(rows, "baseline_arguments_match"),
            deterministic=all(row["deterministic"] for row in rows),
            confidence_calibrated=confidence_calibrated,
            confidence_brier_score=confidence_brier_score,
            confidence_max_brier_score=self._maximum_confidence_brier_score,
            latency_p95_ms=latencies[p95_index],
            memory_peak_bytes=max(max(0, int(case.memory_bytes)) for case in cases),
            slice_accuracy=slice_accuracy,
            baseline_slice_accuracy=baseline_slice_accuracy,
            slice_regressions=slice_regressions,
            passed_required_slices=self.REQUIRED_SLICES.issubset(slices)
            and all(slice_accuracy[slice_id] == 1.0 for slice_id in self.REQUIRED_SLICES),
            evaluation_seed=evaluation_seed,
        )

    @staticmethod
    def _score(case: ToolEvaluationCase) -> dict[str, bool]:
        candidates = case.candidate_decisions
        candidate = candidates[0] if candidates else None
        candidate_mapping: Mapping[str, Any] = candidate if isinstance(candidate, Mapping) else {}
        valid = isinstance(candidate, Mapping)
        if valid:
            try:
                json.dumps(candidate_mapping, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError):
                valid = False
        candidate_tool = candidate_mapping.get("tool") if valid else None
        candidate_args = candidate_mapping.get("arguments") if valid else None
        known_tool = valid and (candidate_tool is None or candidate_tool in case.allowed_tools)
        args_mapping = isinstance(candidate_args, Mapping)
        candidate_arguments: Mapping[str, Any] = candidate_args if isinstance(candidate_args, Mapping) else {}
        required = args_mapping and all(key in candidate_arguments for key in case.required_arguments)
        types = required and all(
            isinstance(candidate_arguments[key], expected_type)
            and not (expected_type in {int, float} and isinstance(candidate_arguments[key], bool))
            for key, expected_type in case.required_arguments.items()
        )
        known_arguments = args_mapping and set(candidate_arguments) == set(case.expected_arguments)
        selection = valid and candidate_tool == case.expected_tool
        arguments = args_mapping and dict(candidate_arguments) == dict(case.expected_arguments)
        base = case.base_decision if isinstance(case.base_decision, Mapping) else {}
        encoded_candidates = []
        for item in candidates:
            try:
                encoded_candidates.append(
                    json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    if isinstance(item, Mapping)
                    else "invalid"
                )
            except (TypeError, ValueError):
                encoded_candidates.append("invalid")
        return {
            "json_valid": valid,
            "known_tool": bool(known_tool),
            "required_fields": bool(required),
            "argument_types": bool(types),
            "known_arguments": bool(known_arguments),
            "selection_match": bool(selection),
            "arguments_match": bool(arguments),
            "baseline_selection_match": base.get("tool") == case.expected_tool,
            "baseline_arguments_match": isinstance(base.get("arguments"), Mapping)
            and dict(base["arguments"]) == dict(case.expected_arguments),
            "deterministic": bool(encoded_candidates) and len(set(encoded_candidates)) == 1,
        }


def _rate(rows: Sequence[Mapping[str, bool]], key: str) -> float:
    return sum(bool(row[key]) for row in rows) / len(rows)


def _digest(value: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("local_adapter_evaluation_digest_invalid")
    return normalized


def _report_rate(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"local_adapter_evaluation_{name}_invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(f"local_adapter_evaluation_{name}_invalid")


def _finite_confidence(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    return None


def _value_digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        encoded = b"invalid-noncanonical-value"
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["LocalAdapterEvaluationReport", "LocalAdapterEvaluationService", "ToolEvaluationCase"]
