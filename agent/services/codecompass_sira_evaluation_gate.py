"""Fail-closed release gate for bound SIRA benchmark reports."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SiraEvaluationGateDecision:
    passed: bool
    policy_sha256: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "policy_sha256": self.policy_sha256,
            "reason_codes": list(self.reason_codes),
        }


class CodeCompassSiraEvaluationGate:
    """Apply one closed policy without executing retrieval or models."""

    _BINDING_FIELDS = frozenset(
        {
            "repository_revision",
            "source_manifest_hash",
            "golden_digest",
            "model_digest",
            "prompt_digest",
            "index_digest",
        }
    )
    _METRICS = frozenset({"recall", "ndcg", "mrr", "evidence_coverage"})
    _POLICY_FIELDS = frozenset(
        {
            "schema",
            "minimum_verified_queries",
            "minimum_repository_count",
            "minimum_aggregate_delta",
            "minimum_delta_ci95_lower",
            "protected_query_classes",
            "maximum_protected_class_regression",
            "efficiency_budgets",
        }
    )

    def assess(self, report: Mapping[str, Any], policy: Mapping[str, Any]) -> SiraEvaluationGateDecision:
        normalized = self._policy(policy)
        reasons: list[str] = []
        binding = report.get("binding")
        if not isinstance(binding, Mapping) or any(
            not str(binding.get(key) or "").strip() for key in self._BINDING_FIELDS
        ):
            reasons.append("sira_gate_binding_incomplete")
        verified = _integer(report.get("verified_query_count"))
        if verified < normalized["minimum_verified_queries"]:
            reasons.append("sira_gate_verified_queries_insufficient")
        repositories = report.get("repositories")
        if not isinstance(repositories, Mapping) or len(repositories) < normalized["minimum_repository_count"]:
            reasons.append("sira_gate_repository_coverage_insufficient")
        aggregate = report.get("aggregate")
        if not isinstance(aggregate, Mapping):
            reasons.append("sira_gate_aggregate_missing")
        else:
            for metric, minimum in normalized["minimum_aggregate_delta"].items():
                row = aggregate.get(_metric_key(aggregate, metric))
                if not isinstance(row, Mapping) or _finite(row.get("delta")) < minimum:
                    reasons.append(f"sira_gate_aggregate_regression:{metric}")
                    continue
                ci = row.get("delta_ci95")
                lower = _finite(ci.get("lower")) if isinstance(ci, Mapping) else -math.inf
                if lower < normalized["minimum_delta_ci95_lower"].get(metric, 0.0):
                    reasons.append(f"sira_gate_uncertainty_regression:{metric}")
        classes = report.get("query_classes")
        if not isinstance(classes, Mapping):
            reasons.append("sira_gate_query_classes_missing")
        else:
            maximum_regression = normalized["maximum_protected_class_regression"]
            for query_class in normalized["protected_query_classes"]:
                group = classes.get(query_class)
                if not isinstance(group, Mapping) or _integer(group.get("verified_query_count")) < 1:
                    reasons.append(f"sira_gate_protected_class_missing:{query_class}")
                    continue
                for metric in normalized["minimum_aggregate_delta"]:
                    row = group.get(_metric_key(group, metric))
                    if not isinstance(row, Mapping) or _finite(row.get("delta")) < -maximum_regression:
                        reasons.append(f"sira_gate_protected_class_regression:{query_class}:{metric}")
        candidate_efficiency = (report.get("efficiency") or {}).get("candidate")
        if not isinstance(candidate_efficiency, Mapping):
            reasons.append("sira_gate_efficiency_missing")
        else:
            for metric, maximum in normalized["efficiency_budgets"].items():
                value = _finite(candidate_efficiency.get(metric))
                if not math.isfinite(value):
                    reasons.append(f"sira_gate_efficiency_unverified:{metric}")
                elif value > maximum:
                    reasons.append(f"sira_gate_efficiency_exceeded:{metric}")
        return SiraEvaluationGateDecision(
            passed=not reasons,
            policy_sha256=_sha256(normalized),
            reason_codes=tuple(reasons or ("sira_evaluation_gate_passed",)),
        )

    def _policy(self, policy: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(policy or {})
        if set(raw) != self._POLICY_FIELDS or raw.get("schema") != "codecompass.sira-evaluation-policy.v1":
            raise ValueError("sira_evaluation_policy_invalid")
        minimum_delta = _metric_thresholds(raw["minimum_aggregate_delta"], "minimum_aggregate_delta")
        minimum_ci = _metric_thresholds(raw["minimum_delta_ci95_lower"], "minimum_delta_ci95_lower")
        protected_value = raw["protected_query_classes"]
        efficiency_value = raw["efficiency_budgets"]
        if (
            not isinstance(protected_value, list)
            or not isinstance(efficiency_value, Mapping)
            or any(not isinstance(item, str) for item in protected_value)
        ):
            raise ValueError("sira_evaluation_policy_invalid")
        protected = tuple(sorted({item.strip() for item in protected_value if item.strip()}))
        efficiency = {
            str(key): _nonnegative(value, f"efficiency_budgets.{key}")
            for key, value in efficiency_value.items()
        }
        if not protected or not efficiency:
            raise ValueError("sira_evaluation_policy_invalid")
        return {
            "schema": raw["schema"],
            "minimum_verified_queries": _positive_integer(raw["minimum_verified_queries"]),
            "minimum_repository_count": _positive_integer(raw["minimum_repository_count"]),
            "minimum_aggregate_delta": minimum_delta,
            "minimum_delta_ci95_lower": minimum_ci,
            "protected_query_classes": protected,
            "maximum_protected_class_regression": _nonnegative(
                raw["maximum_protected_class_regression"],
                "maximum_protected_class_regression",
            ),
            "efficiency_budgets": efficiency,
        }


def _metric_key(values: Mapping[str, Any], metric: str) -> str:
    if metric in {"mrr", "evidence_coverage"}:
        return metric
    prefix = f"{metric}_at_"
    matches = sorted(key for key in values if str(key).startswith(prefix))
    return matches[0] if len(matches) == 1 else metric


def _metric_thresholds(value: object, name: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != CodeCompassSiraEvaluationGate._METRICS:
        raise ValueError(f"sira_evaluation_policy_{name}_invalid")
    return {str(key): _finite_threshold(item, name) for key, item in value.items()}


def _finite_threshold(value: object, name: str) -> float:
    result = _finite(value)
    if not math.isfinite(result) or not -1.0 <= result <= 1.0:
        raise ValueError(f"sira_evaluation_policy_{name}_invalid")
    return result


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return math.nan
    result = float(value)
    return result if math.isfinite(result) else math.nan


def _nonnegative(value: object, name: str) -> float:
    result = _finite(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"sira_evaluation_policy_{name}_invalid")
    return result


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("sira_evaluation_policy_count_invalid")
    return value


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["CodeCompassSiraEvaluationGate", "SiraEvaluationGateDecision"]
