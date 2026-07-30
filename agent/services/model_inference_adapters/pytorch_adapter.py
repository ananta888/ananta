"""Safe PyTorch forward-pass adapter without pickle deserialization."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

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

_ENGINE = "pytorch"
_FORBIDDEN_SUFFIXES = frozenset({".bin", ".ckpt", ".joblib", ".pickle", ".pkl", ".pt", ".pth"})


class SafeTorchModelFactoryRegistry:
    """Code-owned allowlist for custom safetensors state-dict factories."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[Path], tuple[Any, Any]]] = {}

    def register(self, factory_id: str, factory: Callable[[Path], tuple[Any, Any]]) -> None:
        if not factory_id or factory_id in self._factories:
            raise ValueError("factory_id must be non-empty and unique")
        self._factories[factory_id] = factory

    def build(self, factory_id: str, root: Path) -> tuple[Any, Any]:
        try:
            factory = self._factories[factory_id]
        except KeyError as exc:
            raise ValueError("model_factory_unavailable") from exc
        return factory(root)


class PyTorchAdapter(BaseInferenceAdapter):
    ENGINE = _ENGINE
    CAPABILITIES = frozenset({CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_FEATURE_EXTRACTION, CAP_RERANK})
    CAPABILITY_DECLARATIONS = dict.fromkeys(
        CAPABILITIES,
        (SUPPORT_CONDITIONAL, "requires_compatible_model_task"),
    )

    def __init__(
        self,
        *,
        model_id: str | Path,
        tokenizer_path: str | Path | None = None,
        task: str = "feature-extraction",
        device: str = "cpu",
        output_hidden_states: bool = False,
        output_attentions: bool = False,
        labels: list[str] | None = None,
        max_seq_length: int = 512,
        dtype: str = "float32",
        source_kind: str = "hf_safetensors",
        factory_id: str = "",
        factory_registry: SafeTorchModelFactoryRegistry | None = None,
        local_files_only: bool = True,
    ) -> None:
        del output_hidden_states, output_attentions
        self._root = Path(model_id)
        self._model_id = str(model_id)
        self._tokenizer_path = Path(tokenizer_path) if tokenizer_path else self._root
        self._task = str(task).lower()
        self._device = str(device).lower()
        self._labels = tuple(str(label) for label in (labels or ()))
        self._max_seq_length = max(1, int(max_seq_length))
        self._dtype = str(dtype).lower()
        self._source_kind = str(source_kind).lower()
        self._factory_id = str(factory_id)
        self._factory_registry = factory_registry or SafeTorchModelFactoryRegistry()
        self._local_files_only = bool(local_files_only)
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._error = ""
        self._caps: frozenset[str] = frozenset()
        self._try_load()

    def _try_load(self) -> None:
        if self._local_files_only and (not self._root.exists() or not self._root.is_dir()):
            self._error = "local_snapshot_required"
            return
        if self._local_files_only and not self._tokenizer_path.exists():
            self._error = "local_tokenizer_required"
            return
        if any(path.suffix.lower() in _FORBIDDEN_SUFFIXES for path in self._root.rglob("*")):
            self._error = "pickle_artifact_forbidden"
            return
        if self._source_kind not in {"hf_safetensors", "safetensors_state_dict"}:
            self._error = "unsafe_pytorch_source_kind"
            return
        try:
            import torch  # type: ignore[import]

            self._torch = torch
            if self._source_kind == "safetensors_state_dict":
                self._load_registered_state_dict()
            else:
                self._load_hf_safetensors()
            self._model.to(self._device)
            self._model.eval()
            self._caps = (
                frozenset({CAP_CLASSIFICATION})
                if self._task == "sequence-classification"
                else frozenset({CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION, CAP_RERANK})
            )
        except ImportError:
            self._error = "torch_or_safetensors_not_installed"
        except Exception as exc:
            self._model = None
            self._tokenizer = None
            self._error = _safe_error_code(exc)

    def _load_hf_safetensors(self) -> None:
        from transformers import (  # type: ignore[import]
            AutoModel,
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self._tokenizer = AutoTokenizer.from_pretrained(
            str(self._tokenizer_path),
            local_files_only=True,
            trust_remote_code=False,
        )
        model_class = AutoModelForSequenceClassification if self._task == "sequence-classification" else AutoModel
        self._model = model_class.from_pretrained(
            str(self._root),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
            torch_dtype=_torch_dtype(self._torch, self._dtype),
        )

    def _load_registered_state_dict(self) -> None:
        from safetensors.torch import load_file  # type: ignore[import]

        model, tokenizer = self._factory_registry.build(self._factory_id, self._root)
        weights = sorted(self._root.glob("*.safetensors"))
        if len(weights) != 1:
            raise ValueError("state_dict_requires_one_safetensors_file")
        state_dict = load_file(str(weights[0]), device="cpu")
        model.load_state_dict(state_dict, strict=True)
        self._model = model
        self._tokenizer = tokenizer

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name="pytorch",
            engine=_ENGINE,
            status="ready" if self._model is not None else "degraded",
            capabilities=self._caps,
            model_id=self._model_id,
            device=self._device,
            error=self._error,
        )

    def _forward(self, texts: list[str]) -> tuple[Any, dict[str, Any]]:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError(f"PyTorch adapter not ready: {self._error}")
        encoded = self._tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=self._max_seq_length,
        )
        encoded = {key: value.to(self._device) for key, value in encoded.items()}
        with self._torch.inference_mode():
            output = self._model(**encoded)
        return output, encoded

    def embed(self, texts: list[str]) -> list[list[float]]:
        if CAP_EMBEDDINGS not in self._caps:
            raise NotImplementedError("classification model does not expose embeddings")
        output, encoded = self._forward(texts)
        hidden = output.last_hidden_state
        mask = encoded.get("attention_mask")
        if mask is None:
            pooled = hidden.mean(dim=1)
        else:
            weights = mask.unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)
        return [[float(value) for value in row] for row in pooled.detach().float().cpu().tolist()]

    def extract_features(self, text: str) -> FeatureVector:
        vector = self.embed([text])[0]
        return FeatureVector(vector=vector, dimensions=len(vector), model_id=self._model_id, engine=_ENGINE)

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if CAP_CLASSIFICATION not in self._caps:
            raise NotImplementedError("feature model does not classify")
        if not labels or set(labels) != set(self._labels):
            raise ValueError("requested labels do not match manifest labels")
        output, _encoded = self._forward([text])
        probabilities = self._torch.softmax(output.logits[0].float(), dim=-1).detach().cpu().tolist()
        if len(probabilities) != len(self._labels):
            raise RuntimeError("classification output shape mismatch")
        scores = {label: float(probabilities[index]) for index, label in enumerate(self._labels)}
        best = min(scores, key=lambda label: (-scores[label], label))
        return ClassificationResult(best, scores[best], scores, self._model_id, _ENGINE)

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        query_vector = self.embed([query])[0]
        vectors = self.embed([str(item.get("excerpt") or item.get("path") or "") for item in candidates])
        results = [
            RerankResult(
                path=str(candidate.get("path") or ""),
                record_id=str(candidate.get("record_id") or str(index)),
                score=max(0.0, min(1.0, _cosine(query_vector, vector))),
                reason_code="pytorch_cosine",
                model_id=self._model_id,
                engine=_ENGINE,
            )
            for index, (candidate, vector) in enumerate(zip(candidates, vectors))
        ]
        results.sort(key=lambda item: (-item.score, item.path, item.record_id))
        return results

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        if not set(self._labels).issubset({"low", "medium", "high", "critical"}):
            raise ValueError("risk scoring requires fixed risk labels")
        result = self.classify(" ".join(str(value) for value in input_dict.values()), list(self._labels))
        weights = {"low": 0.0, "medium": 1 / 3, "high": 2 / 3, "critical": 1.0}
        score = sum(weights[label] * value for label, value in result.all_scores.items())
        return RiskScoreResult(score, result.label, result.confidence, self._model_id, _ENGINE)

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        raise NotImplementedError("use the HuggingFace causal-logit adapter for fixed-choice scoring")

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        if self._torch is not None and self._device.startswith("cuda"):
            self._torch.cuda.empty_cache()


def _torch_dtype(torch: Any, name: str) -> Any:
    return {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}.get(name, torch.float32)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _safe_error_code(exc: Exception) -> str:
    message = str(exc)
    if message in {
        "model_factory_unavailable",
        "state_dict_requires_one_safetensors_file",
    }:
        return message
    if isinstance(exc, MemoryError) or "out of memory" in message.lower():
        return "out_of_memory"
    return "pytorch_load_failed"
