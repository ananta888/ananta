"""RTIPM-004: HuggingFace Transformers adapter.

Supports: sequence-classification, token-classification, feature-extraction,
causal logit scoring (score_choices with fixed answer set, no generation).

Optional dependency: ``transformers`` + ``torch``.
If not installed, status is ``degraded`` — no crash.

No free text generation is performed. ``generate()`` is never called.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

from agent.services.model_inference_adapters import (
    AdapterStatus,
    BaseInferenceAdapter,
    CAP_ATTENTION,
    CAP_CHOICE_SCORING,
    CAP_CLASSIFICATION,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_HIDDEN_STATES,
    CAP_LOGITS,
    CAP_RERANK,
    ClassificationResult,
    ChoiceScore,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)

log = logging.getLogger(__name__)
_ENGINE = "huggingface-transformers"

_RISK_CATEGORIES = ("low", "medium", "high", "critical")


class HuggingFaceTransformersAdapter(BaseInferenceAdapter):
    """General-purpose HuggingFace Transformers adapter.

    Supports sequence-classification (default task) as well as optional
    feature-extraction and logit choice scoring.  Token-classification and
    other tasks can be added by passing a ``task`` argument.

    The adapter NEVER calls ``model.generate()`` — all outputs are derived
    from encoder hidden states, classification heads or logit probes.
    """

    ENGINE = _ENGINE
    CAPABILITIES: frozenset[str] = frozenset({
        CAP_CLASSIFICATION,
        CAP_EMBEDDINGS,
        CAP_FEATURE_EXTRACTION,
        CAP_HIDDEN_STATES,
        CAP_LOGITS,
        CAP_CHOICE_SCORING,
        CAP_RERANK,
    })

    def __init__(
        self,
        *,
        model_id: str = "distilbert-base-uncased-finetuned-sst-2-english",
        task: str = "sequence-classification",
        device: str = "cpu",
        output_hidden_states: bool = False,
        output_attentions: bool = False,
        revision: str = "main",
    ) -> None:
        self._model_id = model_id
        self._task = task
        self._device = device
        self._output_hidden_states = output_hidden_states
        self._output_attentions = output_attentions
        self._revision = revision
        self._pipeline: Any = None
        self._tokenizer: Any = None
        self._model: Any = None
        self._error = ""
        self._ready = False
        self._caps: frozenset[str] = frozenset()
        self._try_load()

    def _try_load(self) -> None:
        try:
            from transformers import pipeline  # type: ignore[import]
            self._pipeline = pipeline(
                self._task,
                model=self._model_id,
                device=self._device,
                revision=self._revision,
            )
            self._ready = True
            self._caps = frozenset({
                CAP_CLASSIFICATION, CAP_FEATURE_EXTRACTION,
                CAP_HIDDEN_STATES, CAP_LOGITS, CAP_CHOICE_SCORING, CAP_RERANK,
            })
        except ImportError:
            self._error = "transformers not installed"
            log.debug("HuggingFaceTransformersAdapter: %s", self._error)
        except Exception as exc:
            self._error = str(exc)
            log.warning("HuggingFaceTransformersAdapter load error: %s", exc)

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name="huggingface_transformers",
            engine=_ENGINE,
            status="ready" if self._ready else "degraded",
            capabilities=self._caps,
            model_id=self._model_id,
            device=self._device,
            revision=self._revision,
            error=self._error,
        )

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if not self._ready:
            raise RuntimeError(f"HuggingFaceTransformersAdapter not ready: {self._error}")
        t0 = time.time()
        if self._task == "zero-shot-classification":
            result = self._pipeline(text, candidate_labels=labels)
            scores = dict(zip(result["labels"], result["scores"]))
            best = result["labels"][0]
            conf = float(result["scores"][0])
        else:
            result = self._pipeline(text)
            if isinstance(result, list):
                result = result[0]
            best = str(result.get("label") or "")
            conf = float(result.get("score") or 0.0)
            scores = {best: conf}
        latency = (time.time() - t0) * 1000
        return ClassificationResult(
            label=best,
            confidence=conf,
            all_scores=scores,
            model_id=self._model_id,
            engine=_ENGINE,
            latency_ms=latency,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._ready:
            raise RuntimeError(f"HuggingFaceTransformersAdapter not ready: {self._error}")
        results = self._pipeline(texts)
        if isinstance(results, list) and results and isinstance(results[0], list):
            # feature-extraction returns list[list[list[float]]] (batch, seq, dim)
            return [_mean_pool(r) for r in results]
        return [[float(r.get("score") or 0.0)] for r in results]

    def extract_features(self, text: str) -> FeatureVector:
        vec = self.embed([text])[0]
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self._model_id, engine=_ENGINE)

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        """Score fixed-choice options via classification head logits.

        Returns scores only for the provided choices; no generation is performed.
        """
        if not self._ready:
            raise RuntimeError(f"HuggingFaceTransformersAdapter not ready: {self._error}")
        results = []
        for choice in choices:
            combined = f"{prompt} [SEP] {choice}"
            try:
                out = self._pipeline(combined)
                if isinstance(out, list):
                    out = out[0]
                score = float(out.get("score") or 0.0)
            except Exception:
                score = 0.0
            results.append(ChoiceScore(choice=choice, score=score, model_id=self._model_id, engine=_ENGINE))
        # Normalise scores to sum = 1
        total = sum(r.score for r in results) or 1.0
        for r in results:
            r.score = round(r.score / total, 6)
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        results: list[RerankResult] = []
        for c in candidates:
            text = c.get("excerpt") or c.get("path") or ""
            try:
                cr = self.classify(f"{query} [SEP] {text}", ["relevant", "irrelevant"])
                score = cr.confidence if cr.label == "relevant" else (1.0 - cr.confidence)
            except Exception:
                score = 0.0
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or ""),
                score=float(max(0.0, min(1.0, score))),
                reason_code="sequence_classification",
                model_id=self._model_id,
                engine=_ENGINE,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        text = " ".join(str(v) for v in input_dict.values() if v)
        labels = ["security_sensitive", "safe"]
        cr = self.classify(text, labels)
        score = cr.confidence if cr.label == "security_sensitive" else (1.0 - cr.confidence)
        category = _score_to_category(score)
        return RiskScoreResult(
            risk_score=round(score, 4),
            risk_category=category,
            confidence=cr.confidence,
            model_id=self._model_id,
            engine=_ENGINE,
        )


def _mean_pool(hidden_states: list[Any]) -> list[float]:
    """Average-pool over token dimension."""
    if not hidden_states:
        return []
    if isinstance(hidden_states[0], (int, float)):
        return [float(x) for x in hidden_states]
    n = len(hidden_states)
    dim = len(hidden_states[0])
    return [sum(float(hidden_states[i][j]) for i in range(n)) / n for j in range(dim)]


def _score_to_category(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.50:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"
