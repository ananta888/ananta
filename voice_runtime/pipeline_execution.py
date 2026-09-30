"""Backend selection and single-candidate bounded execution for the pipeline.

Both collaborators execute already-selected work only: they resolve local
backends and run one candidate through the admission-controlled executor.
"""

from __future__ import annotations

import uuid
from typing import Callable, cast

from .backends.base import (
    TranscriptionCandidate,
    TranscriptionResult,
    VoiceBackend,
    VoiceBackendResolver,
)
from .config import VoiceRuntimeConfig
from .context import VoiceRecognitionContext
from .errors import VoiceRuntimeError
from .execution_policy import VoiceExecutionPolicy
from .parallel import CandidateExecutionPolicy, ParallelCandidateExecutor
from .pipeline_results import result_from_candidate, stable_candidate_id

LegacyBackendResolver = Callable[[str], VoiceBackend]


class PipelineBackendSelector:
    """Resolve backend identifiers to local backends for one pipeline."""

    def __init__(
        self,
        *,
        config: VoiceRuntimeConfig,
        default_backend: VoiceBackend,
        backend_resolver: VoiceBackendResolver | LegacyBackendResolver | None = None,
    ) -> None:
        self._config = config
        self._backend = default_backend
        self._backend_resolver = backend_resolver

    @property
    def default_backend(self) -> VoiceBackend:
        return self._backend

    def select_for_pipeline(self, pipeline: str, *, policy: VoiceExecutionPolicy) -> VoiceBackend:
        if policy.source == "hub_context":
            return self.backend_for_id(policy.primary_backend)
        if pipeline == "oldschool_light":
            return self.backend_for_id(self._config.asr_backend)
        if pipeline == "whisper_cpp":
            return self.backend_for_id("whisper_cpp")
        if pipeline in {"meeting", "confidence_rerun", "custom", "realtime_streaming"}:
            return self.backend_for_id(self._config.asr_backend)
        return self._backend

    def backend_for_id(self, backend_id: str) -> VoiceBackend:
        normalized = str(backend_id or "").strip().lower()
        if not normalized:
            raise ValueError("unsupported ASR backend: <empty>")
        resolver = self._backend_resolver
        if resolver is not None:
            resolve = getattr(resolver, "resolve", None)
            if callable(resolve):
                return cast(VoiceBackend, resolve(normalized))
            if callable(resolver):
                try:
                    return resolver(normalized)
                except KeyError as exc:
                    raise ValueError(f"unsupported ASR backend: {normalized}") from exc
            raise TypeError("backend_resolver must be callable or implement resolve()")
        if normalized == self._backend.name():
            return self._backend
        raise ValueError(f"unsupported ASR backend without resolver: {normalized}")


class BoundedCandidateRunner:
    """Run exactly one backend candidate under the shared admission budget."""

    def __init__(self, *, candidate_executor: ParallelCandidateExecutor) -> None:
        self._candidate_executor = candidate_executor

    def execute(
        self,
        *,
        backend_id: str,
        backend: VoiceBackend,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
        policy: VoiceExecutionPolicy,
        deadline_seconds: float,
        audio_duration_ms: int,
        source_audio_id: str | None = None,
    ) -> tuple[TranscriptionCandidate, TranscriptionResult | None]:
        effective_source_audio_id = source_audio_id or f"audio-lineage:{uuid.uuid4().hex}"
        batch = self._candidate_executor.execute_batch(
            {backend_id: backend},
            filename=filename,
            content=content,
            language=language,
            context=context,
            policy=CandidateExecutionPolicy(
                max_parallel_backends=1,
                deadline_seconds=max(0.001, deadline_seconds),
                source_audio_id=effective_source_audio_id,
                resource_budget=policy.resource_budget,
                audio_duration_ms=max(0, audio_duration_ms),
            ),
        )
        if not batch.candidates:
            return (
                TranscriptionCandidate.failed(
                    candidate_id=stable_candidate_id(
                        backend_id,
                        effective_source_audio_id,
                        "original",
                    ),
                    backend=backend_id,
                    code="resource_exhausted",
                    message="candidate execution produced no result",
                    retriable=True,
                    source_audio_digest=effective_source_audio_id,
                ),
                None,
            )
        candidate = batch.candidates[0]
        return candidate, batch.results_by_candidate_id.get(candidate.candidate_id)

    def transcribe(
        self,
        *,
        backend_id: str,
        backend: VoiceBackend,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
        policy: VoiceExecutionPolicy,
        deadline_seconds: float,
        audio_duration_ms: int,
    ) -> TranscriptionResult:
        candidate, raw_result = self.execute(
            backend_id=backend_id,
            backend=backend,
            filename=filename,
            content=content,
            language=language,
            context=context,
            policy=policy,
            deadline_seconds=deadline_seconds,
            audio_duration_ms=audio_duration_ms,
        )
        if candidate.status == "succeeded":
            return raw_result or result_from_candidate(candidate)
        error = candidate.error
        if error is not None:
            raise VoiceRuntimeError(error.code, error.message, error.retriable)
        raise RuntimeError("voice backend execution failed")
