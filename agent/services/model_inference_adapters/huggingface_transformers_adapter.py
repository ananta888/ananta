"""Offline-only HuggingFace adapter for fixed, non-generative inference."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from agent.services.model_inference_adapters import (
    CAP_CHOICE_SCORING,
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

log = logging.getLogger(__name__)
_ENGINE = "huggingface-transformers"
_CLASSIFICATION_TASKS = frozenset({"sequence-classification", "token-classification"})
_FEATURE_TASKS = frozenset({"feature-extraction", "embeddings"})
_CHOICE_TASKS = frozenset({"causal-choice-scoring", "causal-lm-choice-scoring"})


class HuggingFaceTransformersAdapter(BaseInferenceAdapter):
    """Load a verified local snapshot with remote code and downloads disabled."""

    ENGINE = _ENGINE
    CAPABILITIES = frozenset(
        {CAP_CLASSIFICATION, CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION, CAP_CHOICE_SCORING, CAP_RERANK}
    )
    CAPABILITY_DECLARATIONS = dict.fromkeys(
        CAPABILITIES,
        (SUPPORT_CONDITIONAL, "requires_compatible_model_task"),
    )

    def __init__(
        self,
        *,
        model_id: str,
        tokenizer_path: str | None = None,
        task: str = "sequence-classification",
        device: str = "cpu",
        output_hidden_states: bool = False,
        output_attentions: bool = False,
        revision: str = "",
        labels: list[str] | None = None,
        max_seq_length: int = 512,
        dtype: str = "float32",
        quantization: str = "none",
        local_files_only: bool = True,
        trust_remote_code: bool = False,
    ) -> None:
        self._model_id = str(model_id)
        self._tokenizer_path = str(tokenizer_path or model_id)
        self._task = str(task).strip().lower()
        self._device = str(device).strip().lower()
        self._revision = str(revision)
        self._configured_labels = tuple(str(label) for label in (labels or ()))
        self._max_seq_length = max(1, int(max_seq_length))
        self._dtype_name = str(dtype).strip().lower()
        self._quantization = str(quantization).strip().lower()
        self._local_files_only = bool(local_files_only)
        self._trust_remote_code = bool(trust_remote_code)
        self._output_hidden_states = bool(output_hidden_states)
        self._output_attentions = bool(output_attentions)
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._error = ""
        self._ready = False
        self._caps: frozenset[str] = frozenset()
        self._labels: tuple[str, ...] = ()
        self._try_load()

    def _try_load(self) -> None:
        if self._trust_remote_code:
            self._error = "remote_code_forbidden"
            return
        if self._quantization != "none":
            self._error = "unsupported_safe_quantization"
            return
        if self._task not in _CLASSIFICATION_TASKS | _FEATURE_TASKS | _CHOICE_TASKS:
            self._error = "unsupported_task"
            return
        if self._local_files_only and not Path(self._model_id).exists():
            self._error = "local_snapshot_required"
            return
        if self._local_files_only and not Path(self._tokenizer_path).exists():
            self._error = "local_tokenizer_required"
            return
        try:
            import torch  # type: ignore[import]
            from transformers import (  # type: ignore[import]
                AutoModel,
                AutoModelForCausalLM,
                AutoModelForSequenceClassification,
                AutoModelForTokenClassification,
                AutoTokenizer,
            )

            self._torch = torch
            common: dict[str, Any] = {
                "local_files_only": self._local_files_only,
                "trust_remote_code": False,
                "use_safetensors": True,
            }
            if self._revision:
                common["revision"] = self._revision
            dtype = _torch_dtype(torch, self._dtype_name)
            if dtype is not None:
                common["torch_dtype"] = dtype
            self._tokenizer = AutoTokenizer.from_pretrained(
                self._tokenizer_path,
                local_files_only=self._local_files_only,
                trust_remote_code=False,
                revision=self._revision or None,
            )
            if self._task == "sequence-classification":
                model_class = AutoModelForSequenceClassification
            elif self._task == "token-classification":
                model_class = AutoModelForTokenClassification
            elif self._task in _CHOICE_TASKS:
                model_class = AutoModelForCausalLM
            else:
                model_class = AutoModel
            self._model = model_class.from_pretrained(self._model_id, **common)
            self._model.to(self._device)
            self._model.eval()
            self._labels = self._resolve_labels()
            self._caps = self._task_capabilities()
            self._ready = True
        except ImportError:
            self._error = "transformers_or_torch_not_installed"
        except Exception as exc:
            self._error = _safe_error_code(exc)
            log.warning("HuggingFace adapter load failed for local snapshot: %s", self._error)

    def _resolve_labels(self) -> tuple[str, ...]:
        if self._configured_labels:
            return self._configured_labels
        raw = getattr(getattr(self._model, "config", None), "id2label", {}) or {}
        try:
            return tuple(str(raw[index]) for index in sorted(int(key) for key in raw))
        except (KeyError, TypeError, ValueError):
            return tuple(str(value) for _, value in sorted(raw.items(), key=lambda item: str(item[0])))

    def _task_capabilities(self) -> frozenset[str]:
        if self._task in _CLASSIFICATION_TASKS:
            capabilities = {CAP_CLASSIFICATION}
            if len(self._labels) == 2:
                capabilities.add(CAP_RERANK)
            return frozenset(capabilities)
        if self._task in _FEATURE_TASKS:
            return frozenset({CAP_EMBEDDINGS, CAP_FEATURE_EXTRACTION})
        if self._task in _CHOICE_TASKS:
            return frozenset({CAP_CHOICE_SCORING})
        return frozenset()

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
        self._require_ready(CAP_CLASSIFICATION)
        requested = tuple(labels)
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("labels must be non-empty and unique")
        if not self._labels or set(requested) != set(self._labels):
            raise ValueError("requested labels do not match the manifest/model id2label mapping")
        started = time.monotonic_ns()
        encoded = self._tokenize([text])
        with self._torch.inference_mode():
            output = self._model(**encoded)
        logits = output.logits
        if self._task == "token-classification":
            mask = encoded.get("attention_mask")
            if mask is not None:
                weights = mask.unsqueeze(-1).to(logits.dtype)
                logits = (logits * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)
            else:
                logits = logits.mean(dim=1)
        probabilities = self._torch.softmax(logits[0].float(), dim=-1).detach().cpu().tolist()
        if len(probabilities) != len(self._labels):
            raise RuntimeError("model label dimension does not match configured labels")
        scores = {label: float(probabilities[index]) for index, label in enumerate(self._labels)}
        best = min(scores, key=lambda label: (-scores[label], label))
        return ClassificationResult(
            label=best,
            confidence=scores[best],
            all_scores={label: scores[label] for label in requested},
            model_id=self._model_id,
            engine=_ENGINE,
            latency_ms=(time.monotonic_ns() - started) / 1_000_000,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._require_ready(CAP_EMBEDDINGS)
        if not texts:
            return []
        encoded = self._tokenize(texts)
        with self._torch.inference_mode():
            output = self._model(**encoded)
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

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        self._require_ready(CAP_CHOICE_SCORING)
        if not choices or len(set(choices)) != len(choices):
            raise ValueError("choices must be non-empty and unique")
        scores = [
            ChoiceScore(
                choice=choice,
                score=self._choice_average_log_probability(prompt, choice),
                model_id=self._model_id,
                engine=_ENGINE,
            )
            for choice in choices
        ]
        scores.sort(key=lambda item: (-item.score, item.choice))
        return scores

    def _choice_average_log_probability(self, prompt: str, choice: str) -> float:
        prompt_ids = self._tokenizer(prompt, add_special_tokens=True, return_tensors="pt")["input_ids"][0]
        full_ids = self._tokenizer(prompt + choice, add_special_tokens=True, return_tensors="pt")["input_ids"][0]
        prefix_length = _common_prefix_length(prompt_ids.tolist(), full_ids.tolist())
        if prefix_length == 0 or prefix_length >= len(full_ids):
            raise ValueError("choice does not add scoreable tokens")
        truncated_from = max(0, len(full_ids) - self._max_seq_length)
        full_ids = full_ids[-self._max_seq_length :].unsqueeze(0).to(self._device)
        prefix_length = max(1, prefix_length - truncated_from)
        with self._torch.inference_mode():
            logits = self._model(input_ids=full_ids).logits[0]
        log_probabilities = self._torch.log_softmax(logits.float(), dim=-1)
        token_scores = []
        for token_position in range(prefix_length, full_ids.shape[1]):
            token_id = int(full_ids[0, token_position])
            token_scores.append(float(log_probabilities[token_position - 1, token_id].detach().cpu()))
        if not token_scores:
            raise ValueError("choice has no scoreable tokens")
        return sum(token_scores) / len(token_scores)

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        self._require_ready(CAP_RERANK)
        if len(self._labels) != 2:
            raise ValueError("reranking requires exactly two configured labels")
        positive = next(
            (label for label in self._labels if label.lower() in {"relevant", "yes", "positive"}), self._labels[-1]
        )
        results: list[RerankResult] = []
        for candidate in candidates:
            excerpt = str(candidate.get("excerpt") or candidate.get("path") or "")
            classification = self.classify(f"{query} [SEP] {excerpt}", list(self._labels))
            score = classification.all_scores[positive]
            results.append(
                RerankResult(
                    path=str(candidate.get("path") or ""),
                    record_id=str(candidate.get("record_id") or ""),
                    score=score,
                    confidence=classification.confidence,
                    reason_code="sequence_classification",
                    model_id=self._model_id,
                    engine=_ENGINE,
                )
            )
        results.sort(key=lambda item: (-item.score, item.path, item.record_id))
        return results

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        self._require_ready(CAP_CLASSIFICATION)
        if not set(self._labels).issubset({"low", "medium", "high", "critical"}):
            raise ValueError("risk scoring requires fixed risk-category labels")
        result = self.classify(" ".join(str(value) for value in input_dict.values()), list(self._labels))
        category_score = {"low": 0.0, "medium": 1 / 3, "high": 2 / 3, "critical": 1.0}
        score = sum(category_score[label] * probability for label, probability in result.all_scores.items())
        return RiskScoreResult(
            risk_score=float(score),
            risk_category=result.label,
            confidence=result.confidence,
            model_id=self._model_id,
            engine=_ENGINE,
        )

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        self._ready = False
        if self._torch is not None and self._device.startswith("cuda"):
            self._torch.cuda.empty_cache()

    def _tokenize(self, texts: list[str]) -> dict[str, Any]:
        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self._max_seq_length,
            return_tensors="pt",
        )
        return {key: value.to(self._device) for key, value in encoded.items()}

    def _require_ready(self, capability: str) -> None:
        if not self._ready:
            raise RuntimeError(f"HuggingFaceTransformersAdapter not ready: {self._error}")
        if capability not in self._caps:
            raise NotImplementedError(f"task {self._task!r} does not provide {capability!r}")


def _torch_dtype(torch: Any, name: str) -> Any:
    return {
        "float32": torch.float32,
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
    }.get(name)


def _common_prefix_length(left: list[int], right: list[int]) -> int:
    length = 0
    for left_token, right_token in zip(left, right):
        if left_token != right_token:
            break
        length += 1
    return length


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
        return "out_of_memory"
    if isinstance(exc, OSError):
        return "local_model_unavailable"
    return "model_load_failed"
