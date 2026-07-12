from __future__ import annotations

import math

import pytest

from agent.services.model_inference_adapters import ChoiceScore, ClassificationResult
from agent.services.path_ai_mode_policy_service import PathAiModePolicyService
from agent.services.restricted_inference_result_guard import RestrictedInferenceResultError
from agent.services.restricted_model_inference_service import (
    MockInferenceAdapter,
    RestrictedModelInferenceService,
)


def _service(adapter: MockInferenceAdapter) -> RestrictedModelInferenceService:
    return RestrictedModelInferenceService(
        adapters=[adapter],
        policy_service=PathAiModePolicyService(),
        use_mock_fallback=False,
    )


class _GeneratedChoiceAdapter(MockInferenceAdapter):
    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        return [ChoiceScore(choice=choices[0], score=1.0, no_generation=False)]


class _InventedLabelAdapter(MockInferenceAdapter):
    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        return ClassificationResult(label="generated-label", confidence=1.0, all_scores={"generated-label": 1.0})


class _ExtraFieldAdapter(MockInferenceAdapter):
    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        result = ChoiceScore(choice=choices[0], score=1.0)
        result.generated_text = "invented text"
        return [result]


class _InvalidEmbeddingAdapter(MockInferenceAdapter):
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[math.nan] for _ in texts]


def test_service_rejects_no_generation_false_and_audits_violation() -> None:
    service = _service(_GeneratedChoiceAdapter())

    with pytest.raises(RestrictedInferenceResultError) as exc_info:
        service.score_choices("question", ["yes"])

    assert exc_info.value.reason_code == "generation_boundary_violation"
    assert service.audit_log()[-1]["event"] == "model_inference_blocked"
    assert service.audit_log()[-1]["reason_code"] == "generation_boundary_violation"


def test_service_rejects_choice_result_with_dynamic_generation_field() -> None:
    with pytest.raises(RestrictedInferenceResultError) as exc_info:
        _service(_ExtraFieldAdapter()).score_choices("question", ["yes"])

    assert exc_info.value.reason_code == "unexpected_result_field"


def test_service_rejects_classification_label_outside_caller_allowlist() -> None:
    with pytest.raises(RestrictedInferenceResultError) as exc_info:
        _service(_InventedLabelAdapter()).classify("question", ["safe", "unsafe"])

    assert exc_info.value.reason_code == "classification_label_outside_allowlist"


def test_service_rejects_non_finite_embedding() -> None:
    with pytest.raises(RestrictedInferenceResultError) as exc_info:
        _service(_InvalidEmbeddingAdapter()).embed(["question"])

    assert exc_info.value.reason_code == "invalid_numeric_result"


def test_service_rejects_duplicate_fixed_choices_before_adapter_call() -> None:
    with pytest.raises(ValueError, match="unique choices"):
        _service(MockInferenceAdapter()).score_choices("question", ["yes", "yes"])


def test_service_rejects_empty_classification_label_set_before_adapter_call() -> None:
    with pytest.raises(ValueError, match="non-empty string labels"):
        _service(MockInferenceAdapter()).classify("question", [])
