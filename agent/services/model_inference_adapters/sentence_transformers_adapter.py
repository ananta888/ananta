"""Offline Sentence-Transformers bi-encoder and CrossEncoder adapters."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from agent.services.model_inference_adapters import (
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
    SUPPORT_CONDITIONAL,
    AdapterStatus,
    BaseInferenceAdapter,
    ChoiceScore,
    ClassificationResult,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)

_ENGINE = "sentence-transformers"


class SentenceTransformerBiEncoderAdapter(BaseInferenceAdapter):
    """Normalized embeddings from one verified local SentenceTransformer."""

    ENGINE = _ENGINE
    CAPABILITIES = frozenset({CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION})

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "cpu",
        max_seq_length: int = 512,
        normalize_embeddings: bool = True,
        local_files_only: bool = True,
    ) -> None:
        self._model_id = str(model_path)
        self._device = device
        self._max_seq_length = max(1, int(max_seq_length))
        self._normalize = bool(normalize_embeddings)
        self._model: Any = None
        self._error = ""
        if local_files_only and not Path(self._model_id).exists():
            self._error = "local_snapshot_required"
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]

            self._model = SentenceTransformer(
                self._model_id,
                device=device,
                local_files_only=local_files_only,
                trust_remote_code=False,
                model_kwargs={"use_safetensors": True},
            )
            self._model.max_seq_length = self._max_seq_length
        except ImportError:
            self._error = "sentence_transformers_not_installed"
        except Exception as exc:
            self._error = _safe_error_code(exc)

    def status(self) -> AdapterStatus:
        ready = self._model is not None
        return AdapterStatus(
            name="sentence_transformers_bi_encoder",
            engine=_ENGINE,
            status="ready" if ready else "degraded",
            capabilities=self.CAPABILITIES if ready else frozenset(),
            model_id=self._model_id,
            device=self._device,
            error=self._error,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError(f"SentenceTransformer bi-encoder not ready: {self._error}")
        vectors = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        result = [[float(value) for value in vector] for vector in vectors]
        if self._normalize:
            for vector in result:
                norm = math.sqrt(sum(value * value for value in vector))
                if vector and not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
                    raise RuntimeError("embedding_normalization_failed")
        return result

    def extract_features(self, text: str) -> FeatureVector:
        vector = self.embed([text])[0]
        return FeatureVector(vector=vector, dimensions=len(vector), model_id=self._model_id, engine=_ENGINE)

    def close(self) -> None:
        self._model = None

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        raise NotImplementedError("bi-encoder does not classify")

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        raise NotImplementedError("use a dedicated CrossEncoder manifest for reranking")

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        raise NotImplementedError("bi-encoder does not score choices")

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        raise NotImplementedError("bi-encoder does not score risk")


class SentenceTransformerCrossEncoderAdapter(BaseInferenceAdapter):
    """Dedicated pairwise reranker with stable tie resolution."""

    ENGINE = _ENGINE
    CAPABILITIES = frozenset({CAP_RERANK})

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "cpu",
        max_seq_length: int = 512,
        local_files_only: bool = True,
    ) -> None:
        self._model_id = str(model_path)
        self._device = device
        self._model: Any = None
        self._error = ""
        if local_files_only and not Path(self._model_id).exists():
            self._error = "local_snapshot_required"
            return
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]

            self._model = CrossEncoder(
                self._model_id,
                device=device,
                max_length=max(1, int(max_seq_length)),
                local_files_only=local_files_only,
                trust_remote_code=False,
                automodel_args={"use_safetensors": True},
            )
        except ImportError:
            self._error = "sentence_transformers_not_installed"
        except Exception as exc:
            self._error = _safe_error_code(exc)

    def status(self) -> AdapterStatus:
        ready = self._model is not None
        return AdapterStatus(
            name="sentence_transformers_cross_encoder",
            engine=_ENGINE,
            status="ready" if ready else "degraded",
            capabilities=self.CAPABILITIES if ready else frozenset(),
            model_id=self._model_id,
            device=self._device,
            error=self._error,
        )

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        if self._model is None:
            raise RuntimeError(f"SentenceTransformer CrossEncoder not ready: {self._error}")
        pairs = [(query, str(item.get("excerpt") or item.get("path") or "")) for item in candidates]
        raw_scores = self._model.predict(pairs, show_progress_bar=False)
        results = []
        for index, (candidate, raw_score) in enumerate(zip(candidates, raw_scores)):
            scalar = _scalar_score(raw_score)
            score = _bounded_probability(scalar)
            results.append(
                RerankResult(
                    path=str(candidate.get("path") or ""),
                    record_id=str(candidate.get("record_id") or str(index)),
                    score=score,
                    confidence=score,
                    reason_code="cross_encoder",
                    model_id=self._model_id,
                    engine=_ENGINE,
                )
            )
        results.sort(key=lambda item: (-item.score, item.path, item.record_id))
        return results

    def close(self) -> None:
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("CrossEncoder does not expose embeddings")

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        raise NotImplementedError("CrossEncoder is restricted to reranking")

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        raise NotImplementedError("CrossEncoder is restricted to reranking")

    def extract_features(self, text: str) -> FeatureVector:
        raise NotImplementedError("CrossEncoder does not expose features")

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        raise NotImplementedError("CrossEncoder is restricted to reranking")


class SentenceTransformersAdapter(BaseInferenceAdapter):
    """Compatibility facade; production manifests select exactly one mode."""

    ENGINE = _ENGINE
    CAPABILITIES = frozenset({CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION, CAP_RERANK})
    CAPABILITY_DECLARATIONS = dict.fromkeys(
        CAPABILITIES,
        (SUPPORT_CONDITIONAL, "requires_sentence_transformers_mode"),
    )

    def __init__(
        self,
        *,
        embedding_model: str,
        cross_encoder_model: str | None = None,
        device: str = "cpu",
        lang_detect: bool = False,
        lang_model_map: dict[str, str] | None = None,
        max_seq_length: int = 512,
        normalize_embeddings: bool = True,
        local_files_only: bool = True,
        mode: str = "bi_encoder",
    ) -> None:
        del lang_detect, lang_model_map
        self._mode = mode
        if mode == "cross_encoder":
            self._delegate: BaseInferenceAdapter = SentenceTransformerCrossEncoderAdapter(
                model_path=cross_encoder_model or embedding_model,
                device=device,
                max_seq_length=max_seq_length,
                local_files_only=local_files_only,
            )
        else:
            self._delegate = SentenceTransformerBiEncoderAdapter(
                model_path=embedding_model,
                device=device,
                max_seq_length=max_seq_length,
                normalize_embeddings=normalize_embeddings,
                local_files_only=local_files_only,
            )

    def status(self) -> AdapterStatus:
        return self._delegate.status()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._delegate.embed(texts)

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        return self._delegate.rerank(query, candidates)

    def extract_features(self, text: str) -> FeatureVector:
        return self._delegate.extract_features(text)

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        return self._delegate.classify(text, labels)

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        return self._delegate.score_choices(prompt, choices)

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        return self._delegate.risk_score(input_dict)

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()


def _scalar_score(value: Any) -> float:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list):
        if not value:
            return 0.0
        if len(value) == 1:
            return float(value[0])
        exponents = [math.exp(float(item) - max(float(part) for part in value)) for item in value]
        return exponents[-1] / sum(exponents)
    return float(value)


def _bounded_probability(value: float) -> float:
    if 0.0 <= value <= 1.0:
        return value
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return "local_model_unavailable"
    if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
        return "out_of_memory"
    return "model_load_failed"
