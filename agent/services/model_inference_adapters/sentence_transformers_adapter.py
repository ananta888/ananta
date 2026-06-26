"""RTIPM-004: sentence-transformers adapter.

Supports Embeddings and CrossEncoder-based Reranking.
Optional dependency: ``sentence-transformers``.
If not installed, status is ``degraded`` and the adapter falls back gracefully.

Optional language-aware model routing: when ``lang_detect=True``, the query
language is detected via ``langdetect`` and the best matching model from
``lang_model_map`` is used for that call (models are lazy-loaded and cached).

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

# Default language → model mapping used when lang_detect is enabled.
_DEFAULT_LANG_MODEL_MAP: dict[str, str] = {
    "de": "paraphrase-multilingual-MiniLM-L12-v2",
    "en": "all-MiniLM-L6-v2",
    "*": "paraphrase-multilingual-MiniLM-L12-v2",
}


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
    lang_detect:
        When ``True``, detect query language and route to the best model from
        ``lang_model_map``. Requires ``langdetect`` to be installed.
    lang_model_map:
        Mapping of ISO-639-1 language code → model ID. Key ``"*"`` is the
        fallback for unknown languages. Defaults to ``_DEFAULT_LANG_MODEL_MAP``.
    """

    ENGINE = _ENGINE
    CAPABILITIES: frozenset[str] = frozenset({CAP_EMBEDDINGS, CAP_RERANK})

    def __init__(
        self,
        *,
        embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2",
        cross_encoder_model: str | None = None,
        device: str = "cpu",
        lang_detect: bool = False,
        lang_model_map: dict[str, str] | None = None,
    ) -> None:
        self._emb_model_id = embedding_model
        self._ce_model_id = cross_encoder_model
        self._device = device
        self._lang_detect = lang_detect
        self._lang_model_map: dict[str, str] = dict(lang_model_map or _DEFAULT_LANG_MODEL_MAP)
        self._emb_model: Any = None
        self._ce_model: Any = None
        self._lang_models: dict[str, Any] = {}  # lang-code → SentenceTransformer
        self._error = ""
        self._ready = False
        self._langdetect_available = False
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

        if self._lang_detect:
            try:
                import langdetect  # type: ignore[import]  # noqa: F401
                self._langdetect_available = True
            except ImportError:
                log.warning("SentenceTransformersAdapter: langdetect not installed; lang_detect disabled")

    def status(self) -> AdapterStatus:
        st = "ready" if self._ready else "degraded"
        # CAP_RERANK is available even without a CrossEncoder: _rerank_via_embedding() provides
        # cosine-similarity reranking using the embedding model alone.
        caps = frozenset({CAP_EMBEDDINGS, CAP_RERANK}) if self._ready else frozenset()
        extra: dict[str, Any] = {}
        if self._lang_detect:
            extra["lang_detect"] = self._langdetect_available
            extra["lang_model_map"] = self._lang_model_map
        return AdapterStatus(
            name="sentence_transformers",
            engine=_ENGINE,
            status=st,
            capabilities=caps,
            model_id=self._emb_model_id,
            device=self._device,
            error=self._error,
        )

    # ── Language-aware model routing ─────────────────────────────────────────

    def _detect_lang(self, text: str) -> str:
        if not self._langdetect_available:
            return ""
        try:
            from langdetect import detect  # type: ignore[import]
            return str(detect(text[:500]))
        except Exception:
            return ""

    def _get_model_for_text(self, text: str) -> Any:
        """Return the best embedding model for *text*, respecting lang_detect config."""
        if not self._lang_detect or not self._langdetect_available:
            return self._emb_model

        lang = self._detect_lang(text)
        target_id = self._lang_model_map.get(lang) or self._lang_model_map.get("*") or self._emb_model_id

        if target_id == self._emb_model_id:
            return self._emb_model

        if target_id in self._lang_models:
            return self._lang_models[target_id]

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            log.info("SentenceTransformersAdapter: lazy-loading model '%s' for lang '%s'", target_id, lang)
            model = SentenceTransformer(target_id, device=self._device)
            self._lang_models[target_id] = model
            return model
        except Exception as exc:
            log.warning("SentenceTransformersAdapter: failed to load '%s' (%s), falling back", target_id, exc)
            return self._emb_model

    # ── Public interface ──────────────────────────────────────────────────────

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._ready or self._emb_model is None:
            raise RuntimeError(f"SentenceTransformersAdapter not ready: {self._error}")
        t0 = time.time()
        # Use language-aware routing on the first text as a representative sample.
        model = self._get_model_for_text(texts[0]) if texts else self._emb_model
        vecs = model.encode(texts, convert_to_numpy=True)
        log.debug("embed %d texts in %.1fms", len(texts), (time.time() - t0) * 1000)
        return [list(map(float, v)) for v in vecs]

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        if not self._ce_model:
            return self._rerank_via_embedding(query, candidates)
        pairs = [[query, c.get("excerpt") or c.get("path") or ""] for c in candidates]
        scores: list[float] = list(self._ce_model.predict(pairs))
        results = []
        for c, sc in zip(candidates, scores):
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or ""),
                score=float(max(0.0, min(1.0, (sc + 1) / 2))),
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
        model = self._get_model_for_text(query)
        texts = [query] + [c.get("excerpt") or c.get("path") or "" for c in candidates]
        vecs = model.encode(texts, convert_to_numpy=True)
        q_vec = vecs[0]
        results = []
        for i, c in enumerate(candidates):
            cand_vec = vecs[i + 1]
            sim = _cosine(q_vec, cand_vec)
            # Determine which model_id was used for the result label
            used_model = getattr(model, '_modules', {})
            model_id = self._emb_model_id
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or ""),
                score=float(max(0.0, sim)),
                reason_code="embedding_cosine",
                model_id=model_id,
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
