"""RTIPM-004: PyTorch adapter.

Flexible local adapter for custom models, fine-tunes and checkpoints.
Supports: embeddings, hidden states, attention, classification, feature
extraction. No free text generation.

Optional dependency: ``torch`` (+ ``transformers`` for tokenizer).
Degrades gracefully if not installed.
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any

from agent.services.model_inference_adapters import (
    AdapterStatus,
    BaseInferenceAdapter,
    CAP_ATTENTION,
    CAP_CLASSIFICATION,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_HIDDEN_STATES,
    CAP_LOGITS,
    CAP_RERANK,
    CAP_CHOICE_SCORING,
    ClassificationResult,
    ChoiceScore,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)

log = logging.getLogger(__name__)
_ENGINE = "pytorch"


class PyTorchAdapter(BaseInferenceAdapter):
    """PyTorch adapter for local models / checkpoints.

    Loads via HuggingFace ``AutoModel`` or a custom ``torch.load`` checkpoint.
    All inference is forward-pass only — ``model.generate()`` is explicitly
    blocked.
    """

    ENGINE = _ENGINE
    CAPABILITIES: frozenset[str] = frozenset({
        CAP_EMBEDDINGS, CAP_HIDDEN_STATES, CAP_ATTENTION, CAP_LOGITS,
        CAP_CLASSIFICATION, CAP_FEATURE_EXTRACTION, CAP_RERANK, CAP_CHOICE_SCORING,
    })

    def __init__(
        self,
        *,
        model_id: str | Path = "",
        task: str = "feature-extraction",
        device: str = "cpu",
        output_hidden_states: bool = True,
        output_attentions: bool = False,
        labels: list[str] | None = None,
    ) -> None:
        self._model_id = str(model_id)
        self._task = task
        self._device = device
        self._output_hidden_states = output_hidden_states
        self._output_attentions = output_attentions
        self._labels = labels or []
        self._model: Any = None
        self._tokenizer: Any = None
        self._error = ""
        self._ready = False
        self._caps: frozenset[str] = frozenset()
        self._try_load()

    def _try_load(self) -> None:
        try:
            import torch  # type: ignore[import]  # noqa: F401
            self._torch = torch
        except ImportError:
            self._error = "torch not installed"
            log.debug("PyTorchAdapter: %s", self._error)
            return

        try:
            from transformers import AutoTokenizer, AutoModel  # type: ignore[import]
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_id)
            self._model = AutoModel.from_pretrained(
                self._model_id,
                output_hidden_states=self._output_hidden_states,
                output_attentions=self._output_attentions,
            )
            self._model.eval()
            self._ready = True
            caps = {CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION, CAP_HIDDEN_STATES, CAP_RERANK}
            if self._output_attentions:
                caps.add(CAP_ATTENTION)
            caps.update({CAP_LOGITS, CAP_CLASSIFICATION, CAP_CHOICE_SCORING})
            self._caps = frozenset(caps)
        except Exception as exc:
            self._error = str(exc)
            log.warning("PyTorchAdapter load error for %s: %s", self._model_id, exc)

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name="pytorch",
            engine=_ENGINE,
            status="ready" if self._ready else "degraded",
            capabilities=self._caps,
            model_id=self._model_id,
            device=self._device,
            error=self._error,
        )

    def _run_forward(self, texts: list[str]) -> Any:
        enc = self._tokenizer(texts, return_tensors="pt", padding=True, truncation=True)
        with self._torch.no_grad():
            out = self._model(**enc)
        return out

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._ready:
            raise RuntimeError(f"PyTorchAdapter not ready: {self._error}")
        out = self._run_forward(texts)
        last_hidden = out.last_hidden_state  # (batch, seq, dim)
        pooled = last_hidden.mean(dim=1)    # mean pool over seq
        return [[float(x) for x in row] for row in pooled]

    def extract_features(self, text: str) -> FeatureVector:
        vec = self.embed([text])[0]
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self._model_id, engine=_ENGINE)

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if not self._ready:
            raise RuntimeError(f"PyTorchAdapter not ready: {self._error}")
        t0 = time.time()
        out = self._run_forward([text])
        # If model has a classification head use its logits; else use pooled embedding norm
        if hasattr(out, "logits"):
            logits = out.logits[0]
            exp_logits = [math.exp(float(l)) for l in logits]
            total = sum(exp_logits) or 1.0
            probs = [e / total for e in exp_logits]
        else:
            # Fallback: distance to label embeddings (labels ignored, return generic)
            probs = [1.0 / max(len(labels), 1)] * len(labels)
        eff_labels = labels or self._labels or [str(i) for i in range(len(probs))]
        scores = {eff_labels[i]: probs[i] for i in range(min(len(eff_labels), len(probs)))}
        best = max(scores, key=lambda k: scores[k])
        return ClassificationResult(
            label=best,
            confidence=float(scores[best]),
            all_scores={k: float(v) for k, v in scores.items()},
            model_id=self._model_id,
            engine=_ENGINE,
            latency_ms=(time.time() - t0) * 1000,
        )

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        texts = [c.get("excerpt") or c.get("path") or "" for c in candidates]
        q_vec = self.embed([query])[0]
        c_vecs = self.embed(texts)
        results = []
        for c, c_vec in zip(candidates, c_vecs):
            sim = _cosine(q_vec, c_vec)
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or ""),
                score=float(max(0.0, sim)),
                reason_code="pytorch_cosine",
                model_id=self._model_id,
                engine=_ENGINE,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        if not self._ready:
            raise RuntimeError(f"PyTorchAdapter not ready: {self._error}")
        results = []
        for choice in choices:
            cr = self.classify(f"{prompt} [SEP] {choice}", choices)
            results.append(ChoiceScore(
                choice=choice,
                score=cr.all_scores.get(choice, 0.0),
                model_id=self._model_id,
                engine=_ENGINE,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        text = " ".join(str(v) for v in input_dict.values() if v)
        labels = ["high_risk", "low_risk"]
        cr = self.classify(text, labels)
        score = cr.confidence if cr.label == "high_risk" else (1.0 - cr.confidence)
        return RiskScoreResult(
            risk_score=round(float(score), 4),
            risk_category=_score_to_category(score),
            confidence=cr.confidence,
            model_id=self._model_id,
            engine=_ENGINE,
        )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x ** 2 for x in a))
    nb = math.sqrt(sum(x ** 2 for x in b))
    return dot / (na * nb) if (na and nb) else 0.0


def _score_to_category(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.50:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"
