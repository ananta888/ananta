"""Deterministic, dependency-free restricted inference adapter.

Used by tests and as the degraded-state fallback of the restricted model
inference gateway; it implements :class:`BaseInferenceAdapter` without any ML
library and never generates free text (LSP-substitutable for real adapters)."""

from __future__ import annotations

from typing import Any

from agent.services.model_inference_adapters import (
    CAP_CHOICE_SCORING,
    CAP_CLASSIFICATION,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
    AdapterStatus,
    BaseInferenceAdapter,
    ChoiceScore,
    ClassificationResult,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)


class MockInferenceAdapter(BaseInferenceAdapter):
    """Deterministic mock adapter for tests and degraded-state fallback.

    All scores are derived from text length / position — reproducible without
    any ML library. No generation.
    """

    ENGINE = "mock"
    CAPABILITIES = frozenset(
        {
            CAP_EMBEDDINGS,
            CAP_CLASSIFICATION,
            CAP_RERANK,
            CAP_CHOICE_SCORING,
            CAP_FEATURE_EXTRACTION,
        }
    )
    MODEL_ID = "mock-deterministic-v1"

    def __init__(self, dims: int = 8) -> None:
        self._dims = max(1, dims)

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name="mock",
            engine=self.ENGINE,
            status="ready",
            capabilities=self.CAPABILITIES,
            model_id=self.MODEL_ID,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            seed = sum(ord(c) for c in text)
            vec = [float((seed + i) % 100) / 100.0 for i in range(self._dims)]
            result.append(vec)
        return result

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if not labels:
            labels = ["positive", "negative"]
        seed = sum(ord(c) for c in text)
        idx = seed % len(labels)
        scores = {label: 1.0 / len(labels) for label in labels}
        scores[labels[idx]] = 0.6
        total = sum(scores.values())
        scores = {k: round(v / total, 4) for k, v in scores.items()}
        return ClassificationResult(
            label=labels[idx],
            confidence=scores[labels[idx]],
            all_scores=scores,
            model_id=self.MODEL_ID,
            engine=self.ENGINE,
        )

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        q_seed = sum(ord(c) for c in query)
        results = []
        for i, c in enumerate(candidates):
            excerpt = str(c.get("excerpt") or c.get("path") or "")
            common = len(set(query.lower().split()) & set(excerpt.lower().split()))
            score = round(min(1.0, common / max(len(query.split()), 1) + (q_seed % 10) / 100), 4)
            results.append(
                RerankResult(
                    path=str(c.get("path") or ""),
                    record_id=str(c.get("record_id") or str(i)),
                    score=score,
                    reason_code="mock_word_overlap",
                    model_id=self.MODEL_ID,
                    engine=self.ENGINE,
                )
            )
        results.sort(key=lambda r: (-r.score, r.path))
        return results

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        seed = sum(ord(c) for c in prompt)
        results = []
        total_w = sum(len(c) + (seed % 7) for c in choices) or 1
        for choice in choices:
            w = (len(choice) + seed % 7) / total_w
            results.append(ChoiceScore(choice=choice, score=round(w, 4), model_id=self.MODEL_ID, engine=self.ENGINE))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def extract_features(self, text: str) -> FeatureVector:
        vec = self.embed([text])[0]
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self.MODEL_ID, engine=self.ENGINE)

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        text = " ".join(str(v) for v in input_dict.values() if v)
        seed = sum(ord(c) for c in text)
        score = (seed % 100) / 100.0
        cat = "high" if score >= 0.5 else "low"
        return RiskScoreResult(
            risk_score=round(score, 4), risk_category=cat, model_id=self.MODEL_ID, engine=self.ENGINE
        )


__all__ = ["MockInferenceAdapter"]
