"""Adapter registry for restricted model inference.

The registry owns engine-to-factory resolution and lazy imports. It does not
normalize configuration or dispatch operations; those responsibilities remain
with the config and inference services.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent.services.model_inference_adapters import (
    AdapterStatus,
    BaseInferenceAdapter,
    CAP_CLASSIFICATION,
    CAP_CHOICE_SCORING,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
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
        return {
            engine: sorted(reg.capabilities)
            for engine, reg in sorted(self._registrations.items())
        }

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
        statuses: list[AdapterStatus] = []
        seen_model_ids: set[str] = set()
        for model in models:
            if not model.enabled:
                statuses.append(AdapterStatus(
                    name=model.engine,
                    engine=model.engine,
                    status="unavailable",
                    model_id=model.id,
                    device=model.device,
                    revision=model.revision,
                    error="disabled_model",
                ))
                continue
            if model.engine not in self._registrations:
                statuses.append(AdapterStatus(
                    name=model.engine,
                    engine=model.engine,
                    status="unavailable",
                    model_id=model.id,
                    error="unknown_engine",
                ))
                continue
            try:
                status = self.build(model).status()
            except Exception as exc:
                status = AdapterStatus(
                    name=model.engine,
                    engine=model.engine,
                    status="unavailable",
                    model_id=model.id,
                    device=model.device,
                    revision=model.revision,
                    error=str(exc),
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

    return SentenceTransformersAdapter(
        embedding_model=model.local_path or model.model,
        cross_encoder_model=model.options.get("cross_encoder_model"),
        device=model.device,
    )


def _build_huggingface(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.huggingface_transformers_adapter import (
        HuggingFaceTransformersAdapter,
    )

    return HuggingFaceTransformersAdapter(
        model_id=model.local_path or model.model,
        task=str(model.options.get("task") or "sequence-classification"),
        device=model.device,
        output_hidden_states=bool(model.options.get("allow_hidden_states", False)),
        output_attentions=bool(model.options.get("allow_attention", False)),
        revision=model.revision or "main",
    )


def _build_onnxruntime(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.onnxruntime_adapter import OnnxRuntimeAdapter

    return OnnxRuntimeAdapter(
        model_path=model.local_path or model.model,
        tokenizer_path=model.options.get("tokenizer_path"),
        labels=[str(item) for item in (model.options.get("labels") or [])],
        device=model.device,
        model_id=model.id,
    )


def _build_pytorch(model: RestrictedInferenceModelConfig) -> BaseInferenceAdapter:
    from agent.services.model_inference_adapters.pytorch_adapter import PyTorchAdapter

    return PyTorchAdapter(
        model_id=model.local_path or model.model,
        task=str(model.options.get("task") or "feature-extraction"),
        device=model.device,
        output_hidden_states=bool(model.options.get("allow_hidden_states", True)),
        output_attentions=bool(model.options.get("allow_attention", False)),
        labels=[str(item) for item in (model.options.get("labels") or [])],
    )


_registry: ModelInferenceAdapterRegistry | None = None


def get_model_inference_adapter_registry() -> ModelInferenceAdapterRegistry:
    global _registry
    if _registry is None:
        _registry = ModelInferenceAdapterRegistry()
    return _registry
