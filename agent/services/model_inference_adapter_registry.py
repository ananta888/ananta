"""Adapter registry for restricted model inference.

The registry owns engine-to-factory resolution and lazy imports. It does not
normalize configuration or dispatch operations; those responsibilities remain
with the config and inference services.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Callable

from agent.services.model_inference_adapters import (
    CAP_CHOICE_SCORING,
    CAP_CLASSIFICATION,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
    AdapterStatus,
    BaseInferenceAdapter,
)
from agent.services.restricted_inference_config_service import (
    ENGINE_HUGGINGFACE,
    ENGINE_MOCK,
    ENGINE_ONNXRUNTIME,
    ENGINE_PYTORCH,
    ENGINE_SENTENCE_TRANSFORMERS,
    KNOWN_ENGINES,
    RestrictedInferenceModelConfig,
)

AdapterFactory = Callable[[RestrictedInferenceModelConfig], BaseInferenceAdapter]


@dataclass(frozen=True)
class EngineRegistration:
    engine: str
    factory: AdapterFactory
    capabilities: frozenset[str] = field(default_factory=frozenset)


class ModelInferenceAdapterRegistry:
    """Registry mapping restricted inference engine names to factories."""

    def __init__(self) -> None:
        self._registrations: dict[str, EngineRegistration] = {}
        self.register(
            ENGINE_MOCK,
            _build_mock,
            frozenset({CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_RERANK, CAP_CHOICE_SCORING, CAP_FEATURE_EXTRACTION}),
        )
        self.register(
            ENGINE_SENTENCE_TRANSFORMERS,
            _build_sentence_transformers,
            frozenset({CAP_EMBEDDINGS, CAP_RERANK, CAP_FEATURE_EXTRACTION}),
        )
        self.register(
            ENGINE_HUGGINGFACE,
            _build_huggingface,
            frozenset({CAP_CLASSIFICATION, CAP_CHOICE_SCORING, CAP_FEATURE_EXTRACTION, CAP_RERANK}),
        )
        self.register(
            ENGINE_ONNXRUNTIME,
            _build_onnxruntime,
            frozenset({CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_FEATURE_EXTRACTION, CAP_RERANK}),
        )
        self.register(
            ENGINE_PYTORCH,
            _build_pytorch,
            frozenset({CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_CHOICE_SCORING, CAP_FEATURE_EXTRACTION, CAP_RERANK}),
        )

    def register(self, engine: str, factory: AdapterFactory, capabilities: frozenset[str]) -> None:
        self._registrations[str(engine)] = EngineRegistration(str(engine), factory, capabilities)

    def engines(self) -> list[str]:
        return sorted(self._registrations)

    def capabilities(self) -> dict[str, list[str]]:
        return {engine: sorted(reg.capabilities) for engine, reg in sorted(self._registrations.items())}

    def build(self, model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
        reg = self._registrations.get(model.engine)
        if reg is None:
            raise ValueError(f"unknown_engine:{model.engine}")
        return reg.factory(model)

    def build_many(self, models: list[RestrictedInferenceModelConfig]) -> list[BaseInferenceAdapter]:
        adapters: list[BaseInferenceAdapter] = []
        for model in models:
            if not model.enabled:
                continue
            if model.engine not in self._registrations:
                continue
            adapters.append(self.build(model))
        return adapters

    def statuses(self, models: list[RestrictedInferenceModelConfig]) -> list[AdapterStatus]:
        """Return declaration/dependency status without constructing adapters.

        Model construction can allocate weights and therefore belongs to the
        isolated worker.  The compatibility ``build`` method remains available
        for explicitly enabled legacy mode and worker-owned factories.
        """
        statuses: list[AdapterStatus] = []
        seen_model_ids: set[str] = set()
        for model in models:
            if not model.enabled:
                statuses.append(
                    AdapterStatus(
                        name=model.engine,
                        engine=model.engine,
                        status="unavailable",
                        model_id=model.id,
                        device=model.device,
                        revision=model.revision,
                        error="disabled_model",
                    )
                )
                continue
            if model.engine not in self._registrations:
                statuses.append(
                    AdapterStatus(
                        name=model.engine,
                        engine=model.engine,
                        status="unavailable",
                        model_id=model.id,
                        error="unknown_engine",
                    )
                )
                continue
            dependency = _ENGINE_DEPENDENCIES.get(model.engine)
            dependency_available = dependency is None or importlib.util.find_spec(dependency) is not None
            status = AdapterStatus(
                name=model.engine,
                engine=model.engine,
                status="declared" if dependency_available else "unavailable",
                capabilities=self._registrations[model.engine].capabilities if dependency_available else frozenset(),
                model_id=model.id,
                device=model.device,
                revision=model.revision,
                error="" if dependency_available else "missing_dependency",
            )
            statuses.append(status)
            seen_model_ids.add(model.id)
        if not seen_model_ids:
            statuses.append(_build_mock(RestrictedInferenceModelConfig(id="mock-default")).status())
        return statuses

    def engine_dependency_status(self, models: list[RestrictedInferenceModelConfig]) -> dict[str, str]:
        status: dict[str, str] = {engine: "unconfigured" for engine in KNOWN_ENGINES}
        for item in self.statuses(models):
            status[item.engine] = item.status
        status[ENGINE_MOCK] = "ready"
        return status


def _build_mock(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.restricted_model_inference_service import MockInferenceAdapter

    dims = int(model.options.get("dimensions") or model.options.get("dims") or 8)
    return MockInferenceAdapter(dims=dims)


def _build_sentence_transformers(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.sentence_transformers_adapter import (
        SentenceTransformersAdapter,
    )

    raw_map = model.options.get("lang_model_map")
    lang_model_map = dict(raw_map) if isinstance(raw_map, dict) else None
    return SentenceTransformersAdapter(
        embedding_model=model.local_path or model.model,
        cross_encoder_model=model.options.get("cross_encoder_model"),
        device=model.device,
        lang_detect=bool(model.options.get("lang_detect", False)),
        lang_model_map=lang_model_map,
        max_seq_length=int(model.options.get("max_seq_length") or 512),
        normalize_embeddings=bool(model.options.get("normalize_embeddings", True)),
        local_files_only=bool(model.options.get("local_files_only", True)),
        mode=str(model.options.get("sentence_mode") or "bi_encoder"),
    )


def _build_huggingface(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.huggingface_transformers_adapter import (
        HuggingFaceTransformersAdapter,
    )

    return HuggingFaceTransformersAdapter(
        model_id=model.local_path or model.model,
        tokenizer_path=model.options.get("tokenizer_path"),
        task=str(model.options.get("task") or "sequence-classification"),
        device=model.device,
        output_hidden_states=bool(model.options.get("allow_hidden_states", False)),
        output_attentions=bool(model.options.get("allow_attention", False)),
        revision=model.revision,
        labels=[str(item) for item in (model.options.get("labels") or [])],
        max_seq_length=int(model.options.get("max_seq_length") or 512),
        dtype=str(model.options.get("dtype") or "float32"),
        quantization=str(model.options.get("quantization") or "none"),
        local_files_only=bool(model.options.get("local_files_only", True)),
        trust_remote_code=bool(model.options.get("trust_remote_code", False)),
    )


def _build_onnxruntime(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.onnxruntime_adapter import OnnxRuntimeAdapter

    return OnnxRuntimeAdapter(
        model_path=model.local_path or model.model,
        tokenizer_path=model.options.get("tokenizer_path"),
        labels=[str(item) for item in (model.options.get("labels") or [])],
        device=model.device,
        model_id=model.id,
        task=str(model.options.get("task") or "feature-extraction"),
        max_seq_length=int(model.options.get("max_seq_length") or 512),
        pooling=str(model.options.get("pooling") or "mean"),
        allowed_external_data=[str(item) for item in (model.options.get("allowed_external_data") or [])],
    )


def _build_pytorch(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.pytorch_adapter import PyTorchAdapter

    return PyTorchAdapter(
        model_id=model.local_path or model.model,
        tokenizer_path=model.options.get("tokenizer_path"),
        task=str(model.options.get("task") or "feature-extraction"),
        device=model.device,
        output_hidden_states=bool(model.options.get("allow_hidden_states", True)),
        output_attentions=bool(model.options.get("allow_attention", False)),
        labels=[str(item) for item in (model.options.get("labels") or [])],
        max_seq_length=int(model.options.get("max_seq_length") or 512),
        dtype=str(model.options.get("dtype") or "float32"),
        source_kind=str(model.options.get("source_kind") or "hf_safetensors"),
        factory_id=str(model.options.get("factory_id") or ""),
        local_files_only=bool(model.options.get("local_files_only", True)),
    )


_ENGINE_DEPENDENCIES = {
    ENGINE_HUGGINGFACE: "transformers",
    ENGINE_ONNXRUNTIME: "onnxruntime",
    ENGINE_PYTORCH: "torch",
    ENGINE_SENTENCE_TRANSFORMERS: "sentence_transformers",
}


_registry: ModelInferenceAdapterRegistry | None = None


def get_model_inference_adapter_registry() -> ModelInferenceAdapterRegistry:
    global _registry
    if _registry is None:
        _registry = ModelInferenceAdapterRegistry()
    return _registry
