"""RTIPM-004: sentence-transformers adapter.

Supports Embeddings and CrossEncoder-based Reranking.
Optional dependency: ``sentence-transformers``.
If not installed, status is ``degraded`` and the adapter falls back gracefully.

No free text generation is performed or returned.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.services.model_inference_adapters import (
    AdapterStatus,
    BaseInferenceAdapter,
    CAP_EMBEDDINGS,
    CAP_RERANK,
    ClassificationResult,
    ChoiceScore,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)

log = logging.getLogger(__name__)

_ENGINE = "sentence-transformers"


class SentenceTransformersAdapter(BaseInferenceAdapter):
    """Embedding and reranking adapter backed by sentence-transformers.

    Parameters
    ----------
    embedding_model:
        HuggingFace model ID or local path for SentenceTransformer.
    cross_encoder_model:
        HuggingFace model ID or local path for CrossEncoder reranking.
        ``None`` disables reranking capability.
    device:
        PyTorch device string (``"cpu"``, ``"cuda"``, …). Defaults to ``"cpu"``.
    """

    ENGINE = _ENGINE
    CAPABILITIES: frozenset[str] = frozenset({CAP_EMBEDDINGS, CAP_RERANK})

    def __init__(
        self,
        *,
        embedding_model: str = "all-MiniLM-L6-v2",
        cross_encoder_model: str | None = None,
        device: str = "cpu",
    ) -> None:
        self._emb_model_id = embedding_model
        self._ce_model_id = cross_encoder_model
        self._device = device
        self._emb_model: Any = None
        self._ce_model: Any = None
        self._error = ""
        self._ready = False
        self._try_load()

    def _try_load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            self._emb_model = SentenceTransformer(self._emb_model_id, device=self._device)
            self._ready = True
        except ImportError:
            self._error = "sentence-transformers not installed"
            log.debug("SentenceTransformersAdapter: %s", self._error)
        except Exception as exc:
            self._error = str(exc)
            log.warning("SentenceTransformersAdapter load error: %s", exc)

        if self._ready and self._ce_model_id:
            try:
                from sentence_transformers import CrossEncoder  # type: ignore[import]
                self._ce_model = CrossEncoder(self._ce_model_id, device=self._device)
            except Exception as exc:
                log.warning("SentenceTransformersAdapter CE load error: %s", exc)

    def status(self) -> AdapterStatus:
        st = "ready" if self._ready else "degraded"
        caps = frozenset({CAP_EMBEDDINGS}) if (self._ready and not self._ce_model) else (
            frozenset({CAP_EMBEDDINGS, CAP_RERANK}) if (self._ready and self._ce_model) else frozenset()
        )
        return AdapterStatus(
            name="sentence_transformers",
            engine=_ENGINE,
            status=st,
            capabilities=caps,
            model_id=self._emb_model_id,
            device=self._device,
            error=self._error,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._ready or self._emb_model is None:
            raise RuntimeError(f"SentenceTransformersAdapter not ready: {self._error}")
        t0 = time.time()
        vecs = self._emb_model.encode(texts, convert_to_numpy=True)
        log.debug("embed %d texts in %.1fms", len(texts), (time.time() - t0) * 1000)
        return [list(map(float, v)) for v in vecs]

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        if not self._ce_model:
            # Fall back to embedding cosine similarity
            return self._rerank_via_embedding(query, candidates)
        pairs = [[query, c.get("excerpt") or c.get("path") or ""] for c in candidates]
        scores: list[float] = list(self._ce_model.predict(pairs))
        results = []
        for c, sc in zip(candidates, scores):
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or ""),
                score=float(max(0.0, min(1.0, (sc + 1) / 2))),  # sigmoid-like normalise
                reason_code="cross_encoder",
                model_id=self._ce_model_id or "",
                engine=_ENGINE,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _rerank_via_embedding(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[RerankResult]:
        if not self._ready or not candidates:
            return []
        texts = [query] + [c.get("excerpt") or c.get("path") or "" for c in candidates]
        vecs = self._emb_model.encode(texts, convert_to_numpy=True)
        q_vec = vecs[0]
        results = []
        for i, c in enumerate(candidates):
            cand_vec = vecs[i + 1]
            sim = _cosine(q_vec, cand_vec)
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or ""),
                score=float(max(0.0, sim)),
                reason_code="embedding_cosine",
                model_id=self._emb_model_id,
                engine=_ENGINE,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        raise NotImplementedError("Use HuggingFaceTransformersAdapter for classification")

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        raise NotImplementedError("Use HuggingFaceTransformersAdapter for choice scoring")

    def extract_features(self, text: str) -> FeatureVector:
        vecs = self.embed([text])
        vec = vecs[0] if vecs else []
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self._emb_model_id, engine=_ENGINE)

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        raise NotImplementedError("Use HuggingFaceTransformersAdapter for risk scoring")


def _cosine(a: Any, b: Any) -> float:
    import math
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    norm_a = math.sqrt(sum(float(x) ** 2 for x in a))
    norm_b = math.sqrt(sum(float(x) ** 2 for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
