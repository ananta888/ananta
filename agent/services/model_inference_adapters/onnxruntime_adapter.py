"""Offline-only ONNX Runtime adapter for verified local model files."""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
from typing import Any

from agent.services.model_inference_adapters import (
    CAP_CLASSIFICATION,
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

_ENGINE = "onnxruntime"
_INPUT_TYPES = {
    "tensor(int32)": "int32",
    "tensor(int64)": "int64",
    "tensor(float)": "float32",
}


class OnnxRuntimeAdapter(BaseInferenceAdapter):
    ENGINE = _ENGINE
    CAPABILITIES = frozenset({CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_FEATURE_EXTRACTION, CAP_RERANK})
    CAPABILITY_DECLARATIONS = dict.fromkeys(
        CAPABILITIES,
        (SUPPORT_CONDITIONAL, "requires_compatible_model_task"),
    )

    def __init__(
        self,
        *,
        model_path: str | Path,
        tokenizer_path: str | Path | None = None,
        labels: list[str] | None = None,
        device: str = "cpu",
        model_id: str = "",
        task: str = "feature-extraction",
        max_seq_length: int = 512,
        pooling: str = "mean",
        allowed_external_data: list[str] | None = None,
    ) -> None:
        self._model_path = Path(model_path)
        self._tokenizer_path = Path(tokenizer_path) if tokenizer_path else self._model_path.parent
        self._labels = tuple(str(label) for label in (labels or ()))
        self._device = str(device).lower()
        self._model_id = model_id or self._model_path.stem
        self._task = str(task).lower()
        self._max_seq_length = max(1, int(max_seq_length))
        self._pooling = str(pooling).lower()
        self._allowed_external_data = frozenset(str(item) for item in (allowed_external_data or ()))
        self._session: Any = None
        self._tokenizer: Any = None
        self._input_specs: dict[str, Any] = {}
        self._error = ""
        self._try_load()

    def _try_load(self) -> None:
        if (
            self._model_path.suffix.lower() != ".onnx"
            or not self._model_path.is_file()
            or self._model_path.is_symlink()
            or self._model_path.stat().st_nlink != 1
        ):
            self._error = "invalid_local_onnx_model"
            return
        if not self._tokenizer_path.exists():
            self._error = "local_tokenizer_required"
            return
        if self._pooling not in {"mean", "cls"}:
            self._error = "unsupported_pooling"
            return
        try:
            self._validate_external_data()
            import onnxruntime as ort  # type: ignore[import]
            from transformers import AutoTokenizer  # type: ignore[import]

            available = set(ort.get_available_providers())
            requested = "CUDAExecutionProvider" if self._device.startswith("cuda") else "CPUExecutionProvider"
            if requested not in available:
                self._error = "execution_provider_unavailable"
                return
            options = ort.SessionOptions()
            options.enable_mem_pattern = True
            options.enable_cpu_mem_arena = True
            self._session = ort.InferenceSession(str(self._model_path), sess_options=options, providers=[requested])
            if self._session.get_providers()[0] != requested:
                raise RuntimeError("execution_provider_mismatch")
            self._input_specs = {item.name: item for item in self._session.get_inputs()}
            if "input_ids" not in self._input_specs:
                raise RuntimeError("missing_input_ids")
            if any(spec.type not in _INPUT_TYPES for spec in self._input_specs.values()):
                raise RuntimeError("unsupported_input_dtype")
            if any(len(spec.shape) != 2 for spec in self._input_specs.values()):
                raise RuntimeError("unsupported_input_shape")
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self._tokenizer_path),
                local_files_only=True,
                trust_remote_code=False,
            )
        except ImportError:
            self._error = "onnxruntime_or_tokenizer_not_installed"
        except Exception as exc:
            self._session = None
            self._error = _safe_error_code(exc)

    def _validate_external_data(self) -> None:
        try:
            import onnx  # type: ignore[import]
            from onnx.external_data_helper import ExternalDataInfo  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("onnx_dependency_required_for_external_data") from exc
        model = onnx.load(str(self._model_path), load_external_data=False)
        referenced: set[str] = set()
        for tensor in model.graph.initializer:
            if tensor.data_location != onnx.TensorProto.EXTERNAL:
                continue
            location = str(ExternalDataInfo(tensor).location).replace("\\", "/")
            path = PurePosixPath(location)
            if not location or path.is_absolute() or ".." in path.parts:
                raise RuntimeError("unsafe_external_data_path")
            normalized = path.as_posix()
            unresolved = self._model_path.parent / normalized
            try:
                resolved = unresolved.resolve(strict=True)
                model_root = self._model_path.parent.resolve(strict=True)
            except OSError as exc:
                raise RuntimeError("unsafe_external_data_path") from exc
            if (
                unresolved.is_symlink()
                or not resolved.is_relative_to(model_root)
                or not resolved.is_file()
                or resolved.stat().st_nlink != 1
            ):
                raise RuntimeError("unsafe_external_data_path")
            referenced.add(normalized)
        if referenced != self._allowed_external_data:
            raise RuntimeError("external_data_manifest_mismatch")

    def status(self) -> AdapterStatus:
        ready = self._session is not None and self._tokenizer is not None
        if self._task == "sequence-classification":
            caps = frozenset({CAP_CLASSIFICATION})
        else:
            caps = frozenset({CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION, CAP_RERANK})
        return AdapterStatus(
            name="onnxruntime",
            engine=_ENGINE,
            status="ready" if ready else "degraded",
            capabilities=caps if ready else frozenset(),
            model_id=self._model_id,
            device=self._device,
            error=self._error,
        )

    def _tokenize(self, texts: list[str]) -> dict[str, Any]:
        if self._tokenizer is None:
            raise RuntimeError("ONNX tokenizer is unavailable")
        import numpy as np  # type: ignore[import]

        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self._max_seq_length,
            return_tensors="np",
        )
        result: dict[str, Any] = {}
        for name, spec in self._input_specs.items():
            if name not in encoded:
                raise RuntimeError(f"missing tokenizer input: {name}")
            result[name] = np.asarray(encoded[name], dtype=_INPUT_TYPES[spec.type])
        return result

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._task == "sequence-classification":
            raise NotImplementedError("classification ONNX model does not expose embeddings")
        inputs = self._tokenize(texts)
        outputs = self._session.run(None, inputs)
        hidden = outputs[0]
        if len(hidden.shape) != 3:
            raise RuntimeError("feature model output must have rank 3")
        if self._pooling == "cls":
            pooled = hidden[:, 0, :]
        elif "attention_mask" in inputs:
            mask = inputs["attention_mask"][..., None]
            pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)
        else:
            pooled = hidden.mean(axis=1)
        return [[float(value) for value in row] for row in pooled]

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if self._task != "sequence-classification":
            raise NotImplementedError("feature ONNX model does not classify")
        if not labels or set(labels) != set(self._labels):
            raise ValueError("requested labels do not match ONNX manifest labels")
        logits = self._session.run(None, self._tokenize([text]))[0]
        if len(logits.shape) != 2 or logits.shape[0] != 1 or logits.shape[1] != len(self._labels):
            raise RuntimeError("classification output shape mismatch")
        raw = [float(value) for value in logits[0]]
        maximum = max(raw)
        exponents = [math.exp(value - maximum) for value in raw]
        total = sum(exponents)
        scores = {label: exponents[index] / total for index, label in enumerate(self._labels)}
        best = min(scores, key=lambda label: (-scores[label], label))
        return ClassificationResult(
            label=best,
            confidence=scores[best],
            all_scores={label: scores[label] for label in labels},
            model_id=self._model_id,
            engine=_ENGINE,
        )

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        query_vector = self.embed([query])[0]
        vectors = self.embed([str(item.get("excerpt") or item.get("path") or "") for item in candidates])
        results = [
            RerankResult(
                path=str(candidate.get("path") or ""),
                record_id=str(candidate.get("record_id") or str(index)),
                score=max(0.0, min(1.0, _cosine(query_vector, vector))),
                reason_code="onnx_embedding_cosine",
                model_id=self._model_id,
                engine=_ENGINE,
            )
            for index, (candidate, vector) in enumerate(zip(candidates, vectors))
        ]
        results.sort(key=lambda item: (-item.score, item.path, item.record_id))
        return results

    def extract_features(self, text: str) -> FeatureVector:
        vector = self.embed([text])[0]
        return FeatureVector(vector=vector, dimensions=len(vector), model_id=self._model_id, engine=_ENGINE)

    def close(self) -> None:
        self._session = None
        self._tokenizer = None

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        raise NotImplementedError("ONNX choice scoring requires a separately verified causal-logit adapter")

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        if not set(self._labels).issubset({"low", "medium", "high", "critical"}):
            raise ValueError("risk scoring requires fixed risk labels")
        result = self.classify(" ".join(str(value) for value in input_dict.values()), list(self._labels))
        weights = {"low": 0.0, "medium": 1 / 3, "high": 2 / 3, "critical": 1.0}
        score = sum(weights[label] * value for label, value in result.all_scores.items())
        return RiskScoreResult(score, result.label, result.confidence, self._model_id, _ENGINE)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _safe_error_code(exc: Exception) -> str:
    message = str(exc)
    if message in {
        "execution_provider_mismatch",
        "external_data_manifest_mismatch",
        "missing_input_ids",
        "onnx_dependency_required_for_external_data",
        "unsafe_external_data_path",
        "unsupported_input_dtype",
        "unsupported_input_shape",
    }:
        return message
    return "onnx_load_failed"
