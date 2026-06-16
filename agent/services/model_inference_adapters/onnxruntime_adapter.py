"""RTIPM-004: ONNX Runtime adapter.

Fast, reproducible local inference for exported embedding / classifier /
reranker models. Optional dependency: ``onnxruntime``.
If not installed, status is ``degraded`` — no crash.

No free text generation is performed.
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
    CAP_CLASSIFICATION,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_LOGITS,
    CAP_RERANK,
    ClassificationResult,
    ChoiceScore,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)

log = logging.getLogger(__name__)
_ENGINE = "onnxruntime"


class OnnxRuntimeAdapter(BaseInferenceAdapter):
    """ONNX Runtime adapter for exported classifier / embedding models.

    ``model_path`` must be a path to an ``.onnx`` file. A matching tokenizer
    (HuggingFace ``tokenizers`` library or ``transformers``) is loaded from
    ``tokenizer_path`` (defaults to same directory as model).

    Only classification and embedding operations are supported; no generation.
    """

    ENGINE = _ENGINE
    CAPABILITIES: frozenset[str] = frozenset({
        CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_FEATURE_EXTRACTION,
        CAP_RERANK, CAP_LOGITS,
    })

    def __init__(
        self,
        *,
        model_path: str | Path,
        tokenizer_path: str | Path | None = None,
        labels: list[str] | None = None,
        device: str = "cpu",
        model_id: str = "",
    ) -> None:
        self._model_path = Path(model_path)
        self._tokenizer_path = Path(tokenizer_path) if tokenizer_path else self._model_path.parent
        self._labels = labels or []
        self._device = device
        self._model_id = model_id or self._model_path.stem
        self._session: Any = None
        self._tokenizer: Any = None
        self._error = ""
        self._ready = False
        self._try_load()

    def _try_load(self) -> None:
        try:
            import onnxruntime as ort  # type: ignore[import]
            providers = ["CPUExecutionProvider"]
            if self._device.startswith("cuda"):
                providers = ["CUDAExecutionProvider"] + providers
            self._session = ort.InferenceSession(str(self._model_path), providers=providers)
            self._ready = True
        except ImportError:
            self._error = "onnxruntime not installed"
            log.debug("OnnxRuntimeAdapter: %s", self._error)
            return
        except Exception as exc:
            self._error = str(exc)
            log.warning("OnnxRuntimeAdapter load error: %s", exc)
            return
        # Load tokenizer (optional — graceful degradation)
        try:
            from transformers import AutoTokenizer  # type: ignore[import]
            self._tokenizer = AutoTokenizer.from_pretrained(str(self._tokenizer_path))
        except Exception as exc:
            log.debug("OnnxRuntimeAdapter: tokenizer load failed: %s", exc)

    def status(self) -> AdapterStatus:
        caps = self.CAPABILITIES if self._ready else frozenset()
        return AdapterStatus(
            name="onnxruntime",
            engine=_ENGINE,
            status="ready" if self._ready else "degraded",
            capabilities=caps,
            model_id=self._model_id,
            device=self._device,
            error=self._error,
        )

    def _tokenize(self, texts: list[str]) -> dict[str, Any]:
        if self._tokenizer is None:
            raise RuntimeError("Tokenizer not available — cannot run ONNX inference")
        import numpy as np  # type: ignore[import]
        enc = self._tokenizer(texts, padding=True, truncation=True, return_tensors="np")
        return {k: v for k, v in enc.items()}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._ready:
            raise RuntimeError(f"OnnxRuntimeAdapter not ready: {self._error}")
        inputs = self._tokenize(texts)
        outputs = self._session.run(None, inputs)
        last_hidden = outputs[0]  # shape: (batch, seq, dim)
        results = []
        for i in range(last_hidden.shape[0]):
            pooled = last_hidden[i].mean(axis=0)
            results.append([float(x) for x in pooled])
        return results

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if not self._ready:
            raise RuntimeError(f"OnnxRuntimeAdapter not ready: {self._error}")
        t0 = time.time()
        inputs = self._tokenize([text])
        outputs = self._session.run(None, inputs)
        logits = outputs[0][0]  # shape: (num_labels,)
        # Softmax
        exp_logits = [math.exp(float(l)) for l in logits]
        total = sum(exp_logits)
        probs = [e / total for e in exp_logits]
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
        if not self._ready:
            raise RuntimeError(f"OnnxRuntimeAdapter not ready: {self._error}")
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
                reason_code="onnx_embedding_cosine",
                model_id=self._model_id,
                engine=_ENGINE,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def extract_features(self, text: str) -> FeatureVector:
        vec = self.embed([text])[0]
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self._model_id, engine=_ENGINE)

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
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
