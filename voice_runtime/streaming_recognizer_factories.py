"""Recognizer factories that bind backends or the Hub-policy pipeline to streams."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .backends.base import VoiceBackend
from .context import VoiceRecognitionContext
from .execution_policy import VoiceExecutionPolicy
from .streaming_fusion import IncrementalFusionRecognizer, StreamingModel
from .streaming_protocol import CONTAINER_MEDIA_TYPES, PCM_S16LE_MEDIA_TYPE
from .streaming_recognizers import (
    BufferedBatchRecognizer,
    BufferedPipelineRecognizer,
    IncrementalBackendCatalog,
    IncrementalPrimaryPipelineRecognizer,
    IncrementalRecognizer,
    PolicyRecognizerFactory,
    RecognizerFactory,
    TranscriptionPipelinePort,
)

if TYPE_CHECKING:
    from .config import VoiceRuntimeConfig


def buffered_recognizer_factory(backend: VoiceBackend) -> RecognizerFactory:
    return lambda filename, language, max_bytes, _media_type: BufferedBatchRecognizer(
        backend,
        filename=filename,
        language=language,
        max_bytes=max_bytes,
    )


def container_safe_recognizer_factory(
    backend: VoiceBackend,
    incremental_factory: RecognizerFactory | None,
) -> RecognizerFactory:
    """Keep container inputs on batch execution after the decode preflight."""

    buffered_factory = buffered_recognizer_factory(backend)
    if incremental_factory is None:
        return buffered_factory

    def create(
        filename: str,
        language: str | None,
        max_bytes: int,
        media_type: str,
    ) -> IncrementalRecognizer:
        factory = buffered_factory if media_type in CONTAINER_MEDIA_TYPES else incremental_factory
        return factory(filename, language, max_bytes, media_type)

    return create


def buffered_pipeline_recognizer_factory(pipeline: TranscriptionPipelinePort) -> PolicyRecognizerFactory:
    return lambda filename, language, max_bytes, media_type, context: BufferedPipelineRecognizer(
        pipeline,
        filename=filename,
        language=language,
        max_bytes=max_bytes,
        media_type=media_type,
        context=context,
    )


def policy_streaming_recognizer_factory(
    pipeline: TranscriptionPipelinePort,
    backend_catalog: IncrementalBackendCatalog,
    runtime_config: VoiceRuntimeConfig,
) -> PolicyRecognizerFactory:
    """Build Hub-selected incremental execution with a bounded final fallback.

    Single recognition may expose provisional output from the selected primary
    backend, while finalization always traverses the normal Hub-policy pipeline.
    Multi-model fusion retains its native incremental final contract.  Opaque
    containers and unavailable incremental adapters stay on bounded batch
    execution and never emit fake partials.
    """

    fallback = buffered_pipeline_recognizer_factory(pipeline)

    def create(
        filename: str,
        language: str | None,
        max_bytes: int,
        media_type: str,
        context: VoiceRecognitionContext,
    ) -> IncrementalRecognizer:
        policy = VoiceExecutionPolicy.resolve(runtime_config, context.configuration)
        enabled = (
            media_type == PCM_S16LE_MEDIA_TYPE and policy.source == "hub_context" and policy.transport_mode == "stream"
        )
        if not enabled:
            return fallback(filename, language, max_bytes, media_type, context)

        if policy.recognition_strategy == "single":
            available = backend_catalog.available_backends((policy.primary_backend,))
            backend = available.get(policy.primary_backend)
            factory = getattr(backend, "create_incremental_recognizer", None)
            if callable(factory):
                try:
                    partial_recognizer = factory(
                        filename=filename,
                        language=language,
                        max_bytes=max_bytes,
                    )
                except Exception:
                    partial_recognizer = None
                if partial_recognizer is not None:
                    final_recognizer = fallback(filename, language, max_bytes, media_type, context)
                    return IncrementalPrimaryPipelineRecognizer(
                        partial_recognizer,
                        cast(BufferedPipelineRecognizer, final_recognizer),
                    )
            return fallback(filename, language, max_bytes, media_type, context)

        fusion_enabled = policy.recognition_strategy in {"parallel_compare", "parallel_fusion"} and bool(
            policy.feature_flags.get("voice_fusion", False)
        )
        if not fusion_enabled:
            return fallback(filename, language, max_bytes, media_type, context)

        requested = tuple(dict.fromkeys((policy.primary_backend, *policy.secondary_backends)))[
            : policy.max_parallel_backends
        ]
        available = backend_catalog.available_backends(requested)
        models: list[StreamingModel] = []
        for backend_id in requested:
            backend = available.get(backend_id)
            factory = getattr(backend, "create_incremental_recognizer", None)
            if not callable(factory):
                continue
            try:
                recognizer = factory(filename=filename, language=language, max_bytes=max_bytes)
                models.append(StreamingModel(backend_id=backend_id, recognizer=recognizer))
            except Exception:
                continue
        if len(models) >= 2:
            return cast(IncrementalRecognizer, IncrementalFusionRecognizer(tuple(models)))
        for model in models:
            try:
                model.recognizer.close()
            except Exception:
                pass
        return fallback(filename, language, max_bytes, media_type, context)

    return create
