"""Fail-closed validation for every restricted-inference result type."""

from __future__ import annotations

import math
import re
from dataclasses import fields
from numbers import Real
from typing import Any, Iterable, Mapping, Sequence

from agent.services.model_inference_adapters import (
    ChoiceScore,
    ClassificationResult,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)
from agent.services.restricted_inference_contract import RestrictedInferenceOperation

_REASON_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_RISK_CATEGORIES = frozenset({"low", "medium", "high", "critical"})


class RestrictedInferenceResultError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _operation(raw: str | RestrictedInferenceOperation) -> RestrictedInferenceOperation:
    try:
        return raw if isinstance(raw, RestrictedInferenceOperation) else RestrictedInferenceOperation(str(raw))
    except ValueError as exc:
        raise RestrictedInferenceResultError("unknown_operation", f"unknown restricted operation: {raw!r}") from exc


def _finite_number(value: Any, *, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise RestrictedInferenceResultError("invalid_numeric_result", f"{name} must be a finite number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise RestrictedInferenceResultError("invalid_numeric_result", f"{name} is below {minimum}")
    if maximum is not None and number > maximum:
        raise RestrictedInferenceResultError("invalid_numeric_result", f"{name} is above {maximum}")
    return number


def _exact_dataclass(value: Any, expected_type: type[Any]) -> None:
    if type(value) is not expected_type:
        raise RestrictedInferenceResultError(
            "invalid_result_type",
            f"expected {expected_type.__name__}, got {type(value).__name__}",
        )
    allowed_fields = {item.name for item in fields(expected_type)}
    actual_fields = set(vars(value))
    if actual_fields != allowed_fields:
        raise RestrictedInferenceResultError(
            "unexpected_result_field",
            f"{expected_type.__name__} contains unexpected fields: {sorted(actual_fields - allowed_fields)}",
        )
    if getattr(value, "no_generation", None) is not True:
        raise RestrictedInferenceResultError(
            "generation_boundary_violation",
            f"{expected_type.__name__} has no_generation=False; free generation is not permitted",
        )


def _metadata(value: Any) -> None:
    for name in ("model_id", "engine"):
        text = getattr(value, name, "")
        if not isinstance(text, str) or len(text) > 512:
            raise RestrictedInferenceResultError("invalid_result_metadata", f"{name} must be a bounded string")
    manifest_digest = getattr(value, "manifest_digest", "")
    if manifest_digest and not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
        raise RestrictedInferenceResultError(
            "invalid_result_metadata",
            "manifest_digest must be empty or a SHA-256",
        )


def _reason_code(value: str) -> None:
    if value and not _REASON_CODE_RE.fullmatch(value):
        raise RestrictedInferenceResultError(
            "invalid_reason_code",
            "reason_code must be a fixed machine-readable identifier",
        )


def _validate_vectors(vectors: Any, expected_count: int | None) -> None:
    if not isinstance(vectors, list):
        raise RestrictedInferenceResultError("invalid_embedding_result", "embeddings must be a list")
    if expected_count is not None and len(vectors) != expected_count:
        raise RestrictedInferenceResultError(
            "embedding_count_mismatch",
            "worker must return exactly one vector per input text",
        )
    for row_index, vector in enumerate(vectors):
        if not isinstance(vector, list):
            raise RestrictedInferenceResultError("invalid_embedding_result", "each embedding must be a list")
        for column_index, number in enumerate(vector):
            _finite_number(number, name=f"vectors[{row_index}][{column_index}]")


def _validate_classification(result: Any, allowed_labels: Iterable[str] | None) -> None:
    _exact_dataclass(result, ClassificationResult)
    _metadata(result)
    labels = {str(item) for item in (allowed_labels or ())}
    if not labels or result.label not in labels:
        raise RestrictedInferenceResultError(
            "classification_label_outside_allowlist",
            f"classification label is not one of the caller-provided labels: {result.label!r}",
        )
    if not isinstance(result.all_scores, dict) or not set(result.all_scores).issubset(labels):
        raise RestrictedInferenceResultError(
            "classification_scores_outside_allowlist",
            "classification scores contain a label outside the caller-provided set",
        )
    _finite_number(result.confidence, name="confidence", minimum=0.0, maximum=1.0)
    for label, score in result.all_scores.items():
        _finite_number(score, name=f"all_scores[{label}]", minimum=0.0, maximum=1.0)


def _validate_rerank(results: Any, candidates: Sequence[Mapping[str, Any]] | None) -> None:
    if not isinstance(results, list):
        raise RestrictedInferenceResultError("invalid_rerank_result", "rerank result must be a list")
    candidate_list = list(candidates or ())
    if len(results) > len(candidate_list):
        raise RestrictedInferenceResultError("invented_rerank_candidate", "rerank returned more items than provided")
    allowed_ids = {str(item.get("record_id") or "") for item in candidate_list if item.get("record_id")}
    allowed_paths = {str(item.get("path") or "") for item in candidate_list if item.get("path")}
    seen: set[tuple[str, str]] = set()
    for result in results:
        _exact_dataclass(result, RerankResult)
        _metadata(result)
        if result.record_id and allowed_ids and result.record_id not in allowed_ids:
            raise RestrictedInferenceResultError("invented_rerank_candidate", result.record_id)
        if result.path and allowed_paths and result.path not in allowed_paths:
            raise RestrictedInferenceResultError("invented_rerank_candidate", result.path)
        identity = (result.record_id, result.path)
        if identity in seen:
            raise RestrictedInferenceResultError("duplicate_rerank_candidate", str(identity))
        seen.add(identity)
        _finite_number(result.score, name="score", minimum=0.0, maximum=1.0)
        _finite_number(result.confidence, name="confidence", minimum=0.0, maximum=1.0)
        _reason_code(result.reason_code)


def validate_choice_scores(results: Any, allowed_choices: Iterable[str] | None = None) -> None:
    if not isinstance(results, list):
        raise RestrictedInferenceResultError("invalid_choice_result", "choice result must be a list")
    choices = [str(item) for item in (allowed_choices or (result.choice for result in results))]
    allowed = set(choices)
    returned: list[str] = []
    for result in results:
        _exact_dataclass(result, ChoiceScore)
        _metadata(result)
        if result.choice not in allowed:
            raise RestrictedInferenceResultError(
                "choice_outside_allowlist",
                f"choice is not caller-provided: {result.choice!r}",
            )
        returned.append(result.choice)
        _finite_number(result.score, name="score")
    if allowed_choices is not None and (len(returned) != len(choices) or set(returned) != allowed):
        raise RestrictedInferenceResultError(
            "choice_set_mismatch",
            "worker must return each caller-provided choice exactly once",
        )
    if len(returned) != len(set(returned)):
        raise RestrictedInferenceResultError("duplicate_choice", "worker returned a duplicate choice")


def _validate_features(result: Any) -> None:
    _exact_dataclass(result, FeatureVector)
    _metadata(result)
    if not isinstance(result.vector, list):
        raise RestrictedInferenceResultError("invalid_feature_result", "feature vector must be a list")
    if result.dimensions != len(result.vector):
        raise RestrictedInferenceResultError("invalid_feature_dimensions", "dimensions do not match vector length")
    for index, number in enumerate(result.vector):
        _finite_number(number, name=f"vector[{index}]")


def _validate_risk(result: Any) -> None:
    _exact_dataclass(result, RiskScoreResult)
    _metadata(result)
    if result.risk_category not in _RISK_CATEGORIES:
        raise RestrictedInferenceResultError("invalid_risk_category", result.risk_category)
    _finite_number(result.risk_score, name="risk_score", minimum=0.0, maximum=1.0)
    _finite_number(result.confidence, name="confidence", minimum=0.0, maximum=1.0)


def validate_restricted_result(
    operation: str | RestrictedInferenceOperation,
    result: Any,
    *,
    allowed_labels: Iterable[str] | None = None,
    allowed_choices: Iterable[str] | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    expected_count: int | None = None,
) -> None:
    """Validate output provenance, shape and the no-generation invariant."""

    op = _operation(operation)
    if op is RestrictedInferenceOperation.EMBED:
        _validate_vectors(result, expected_count)
    elif op is RestrictedInferenceOperation.CLASSIFY:
        _validate_classification(result, allowed_labels)
    elif op is RestrictedInferenceOperation.RERANK:
        _validate_rerank(result, candidates)
    elif op is RestrictedInferenceOperation.SCORE_CHOICES:
        validate_choice_scores(result, allowed_choices)
    elif op is RestrictedInferenceOperation.EXTRACT_FEATURES:
        _validate_features(result)
    elif op is RestrictedInferenceOperation.RISK_SCORE:
        _validate_risk(result)
