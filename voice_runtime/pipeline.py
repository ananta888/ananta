from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable, cast

from .backends.base import (
    TranscriptionCandidate,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
    VoiceBackend,
    VoiceBackendResolver,
)
from .config import VoiceRuntimeConfig
from .context import VoiceRecognitionContext
from .diarization import build_diarization_processor
from .diarization_adapters import (
    LocalDiarizationAdapter,
    PyannoteDiarizationAdapter,
    SafeDiarizationProcessor,
    load_offline_diarization_manifest,
)
from .errors import InvalidAudioError, VoiceRuntimeError
from .execution_policy import VoiceExecutionPolicy
from .fusion import (
    CalibrationProfile,
    CandidateLineageValidator,
    CandidateScorer,
    DeterministicFusionService,
    load_calibration_profiles,
)
from .glossary import Glossary
from .model_manifest import VoiceModelCatalog
from .parallel import CandidateExecutionPolicy, ParallelCandidateExecutor
from .pipeline_extensions import PreparedAudioVariant, prepare_enhancement_variants
from .postprocessing import build_postprocessor
from .preprocessing import (
    AudioDecodeLimits,
    DecodedPcmAudio,
    SafeAudioDecoder,
    build_pcm_vad_processor,
    build_vad_processor,
)
from .preprocessing.audio_decode import AudioDecodeError
from .resources import ResourceAdmissionController, resource_budget_from_config
from .routing import (
    AdaptiveLocalRouter,
    BackendRoute,
    ConfidenceRegion,
    RerunRegion,
    RoutingMeasurements,
    RoutingPolicyEnvelope,
    merge_regional_segments,
)
from .source_correction import (
    SourceCorrectionPort,
    SourceCorrectionRequest,
    SourceCorrectionResult,
    SourceCorrectionService,
)

LegacyBackendResolver = Callable[[str], VoiceBackend]


class TranscriptionPipeline:
    """Configurable Voice Runtime transcription orchestrator."""

    def __init__(
        self,
        *,
        config: VoiceRuntimeConfig,
        backend: VoiceBackend,
        candidate_executor: ParallelCandidateExecutor | None = None,
        fusion_service: DeterministicFusionService | None = None,
        audio_decoder: SafeAudioDecoder | None = None,
        model_catalog: VoiceModelCatalog | None = None,
        backend_resolver: VoiceBackendResolver | LegacyBackendResolver | None = None,
        diarization_adapter: LocalDiarizationAdapter | None = None,
        adaptive_router: AdaptiveLocalRouter | None = None,
        lineage_validator: CandidateLineageValidator | None = None,
        source_correction: SourceCorrectionPort | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._backend = backend
        runtime_resource_budget = resource_budget_from_config(config)
        self._candidate_executor = candidate_executor or ParallelCandidateExecutor(
            max_inflight_candidates=config.max_queue_depth,
            admission_controller=ResourceAdmissionController(runtime_resource_budget),
        )
        self._calibration = load_calibration_profiles(config.calibration_path) if config.calibration_path else {}
        self._candidate_scorer = CandidateScorer(self._calibration)
        self._fusion_service = fusion_service or DeterministicFusionService(self._candidate_scorer)
        # Retained as an additive constructor argument for callers that built the
        # old pipeline directly. Runtime catalog ownership now belongs to the
        # injected resolver, keeping backend lifecycle outside orchestration.
        del model_catalog
        self._audio_decoder = audio_decoder or SafeAudioDecoder(
            limits=AudioDecodeLimits(
                max_encoded_bytes=config.max_audio_mb * 1024 * 1024,
                max_decoded_pcm_bytes=config.max_decoded_pcm_mb * 1024 * 1024,
                max_duration_ms=config.max_audio_duration_sec * 1000,
                ffmpeg_timeout_sec=min(config.timeout_sec, 60),
            )
        )
        self._backend_resolver = backend_resolver
        self._diarization_adapter = diarization_adapter
        self._diarization_adapter_initialized = diarization_adapter is not None
        self._adaptive_router = adaptive_router or AdaptiveLocalRouter()
        self._lineage_validator = lineage_validator or CandidateLineageValidator()
        self._source_correction = source_correction or SourceCorrectionService()

    def correct_source_segment(
        self,
        *,
        request: SourceCorrectionRequest,
        provisional: TranscriptionCandidate,
        source: TranscriptionCandidate | None,
    ) -> SourceCorrectionResult:
        """Execute one Hub-delegated segment correction through fusion alignment.

        This is intentionally a direct execution seam: the runtime never
        creates tasks, retries, or contacts another worker.
        """

        return self._source_correction.correct(
            request=request,
            provisional=provisional,
            source=source,
        )

    def runtime_capabilities(self) -> dict[str, object]:
        diarization: dict[str, object] = {
            "configured_backend": self._config.diarization_backend,
            "available": self._config.diarization_backend in {"none", "off", "disabled", "mock"},
            "reason_code": None,
        }
        if self._config.diarization_backend == "pyannote":
            adapter = self._resolve_diarization_adapter()
            capability = adapter.capability() if adapter is not None else None
            diarization.update(
                {
                    "available": bool(capability and capability.available),
                    "reason_code": capability.reason_code if capability else "adapter_unavailable",
                    "local_only": True,
                    "downloads_allowed": False,
                }
            )
        return {
            "schema_version": "ananta.voice-runtime-capabilities.v1",
            "strict_audio_decode_before_fanout": True,
            "synthetic_fixture_bypass": "mock_only",
            "policy": {
                "owner": "hub",
                "context_field": "configuration",
                "allowed_backends": list(self._config.policy_allowed_backends),
                "recognition_strategies": list(self._config.policy_allowed_recognition_strategies),
                "routing_strategies": list(self._config.policy_allowed_routing_strategies),
                "max_parallel_backends": self._config.max_parallel_backends,
                "max_candidate_count": self._config.max_candidate_count,
                "max_candidate_deadline_sec": self._config.candidate_deadline_sec,
            },
            "enhancement": {
                "enabled": self._config.audio_enhancement_enabled,
                "configured_variants": list(self._config.enhancement_variants),
                "lineage_deduplication": True,
                "pcm_duplicate_suppression": True,
            },
            "diarization": diarization,
            "adaptive_routing": {
                "enabled": self._config.adaptive_routing_enabled,
                "local_only": True,
                "max_total_latency_ms": self._config.adaptive_max_total_latency_ms,
                "max_regional_rerun_ms": self._config.adaptive_max_regional_rerun_ms,
            },
            "judge_boundary": {
                "restricted_choice_execution": "hub_postprocessing_only",
                "restricted_no_generation": True,
                "generative_execution": "hub_postprocessing_only",
                "runtime_worker_to_worker_calls": False,
            },
        }

    def transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None = None,
        context: VoiceRecognitionContext | None = None,
    ) -> TranscriptionResult:
        policy = VoiceExecutionPolicy.resolve(
            self._config,
            context.configuration if context is not None else None,
        )
        decoded_audio = self._decode_at_trust_boundary(
            filename=filename,
            content=content,
            policy=policy,
        )
        if policy.recognition_strategy in {"parallel_compare", "parallel_fusion"}:
            result = self._transcribe_parallel(
                filename=filename,
                content=content,
                language=language,
                context=context,
                policy=policy,
                decoded_audio=decoded_audio,
            )
            return self._finalize_execution(
                result,
                filename=filename,
                content=content,
                language=language,
                context=context,
                decoded_audio=decoded_audio,
                policy=policy,
            )
        if policy.recognition_strategy == "classic_then_correct":
            result = self._transcribe_classic_then_correct(
                filename=filename,
                content=content,
                language=language,
                context=context,
                policy=policy,
                decoded_audio=decoded_audio,
            )
            return self._finalize_execution(
                result,
                filename=filename,
                content=content,
                language=language,
                context=context,
                decoded_audio=decoded_audio,
                policy=policy,
            )
        pipeline = self._config.transcription_pipeline
        if pipeline == "simple":
            selected_backend = (
                self._backend_for_id(policy.primary_backend) if policy.source == "hub_context" else self._backend
            )
            result = self._transcribe_bounded_result(
                backend_id=policy.primary_backend
                if policy.source == "hub_context"
                else selected_backend.name(),
                backend=selected_backend,
                filename=filename,
                content=content,
                language=language,
                context=context,
                policy=policy,
                deadline_seconds=policy.candidate_deadline_sec,
                audio_duration_ms=decoded_audio.duration_ms if decoded_audio else 0,
            )
            result = self._ensure_metadata(
                result,
                pipeline=pipeline,
                stages=({"stage": "asr", "backend": result.raw_backend or selected_backend.name()},),
            )
            return self._finalize_execution(
                result,
                filename=filename,
                content=content,
                language=language,
                context=context,
                decoded_audio=decoded_audio,
                policy=policy,
            )

        asr_backend = self._select_pipeline_backend(pipeline, policy=policy)
        if self._config.vad_backend in {"webrtcvad", "silero"}:
            decoded_audio = decoded_audio or self._decode_required(filename=filename, content=content)
            pcm_vad = build_pcm_vad_processor(
                self._config.vad_backend,
                silero_model_path=self._config.silero_vad_model_path,
                silero_threshold=self._config.silero_vad_threshold,
            )
            pcm_segments = pcm_vad.split(decoded_audio)
            stages: list[dict] = [
                {
                    "stage": "vad",
                    "backend": pcm_vad.name(),
                    "segment_count": len(pcm_segments),
                    "timeline": "absolute_ms",
                },
            ]
            results = [
                self._shift_result(
                    self._transcribe_bounded_result(
                        backend_id=asr_backend.name(),
                        backend=asr_backend,
                        filename=f"segment-{segment.start_ms}.wav",
                        content=segment.audio.to_wav_bytes(),
                        language=language,
                        context=context,
                        policy=policy,
                        deadline_seconds=policy.candidate_deadline_sec,
                        audio_duration_ms=segment.audio.duration_ms,
                    ),
                    offset_ms=segment.start_ms,
                )
                for segment in pcm_segments
            ]
        else:
            container_vad = build_vad_processor(self._config.vad_backend)
            audio_segments = container_vad.split(filename=filename, content=content)
            stages = [
                {"stage": "vad", "backend": container_vad.name(), "segment_count": len(audio_segments)},
            ]
            results = [
                self._transcribe_bounded_result(
                    backend_id=asr_backend.name(),
                    backend=asr_backend,
                    filename=segment.filename,
                    content=segment.content,
                    language=language,
                    context=context,
                    policy=policy,
                    deadline_seconds=policy.candidate_deadline_sec,
                    audio_duration_ms=decoded_audio.duration_ms if decoded_audio else 0,
                )
                for segment in audio_segments
            ]
        result = self._merge_results(results=results, filename=filename, fallback_language=language)
        stages.append(
            {"stage": "asr", "backend": result.raw_backend or asr_backend.name(), "segment_count": len(result.segments)}
        )

        if decoded_audio is None and (self._config.confidence_rerun_enabled or pipeline == "confidence_rerun"):
            try:
                decoded_audio = self._audio_decoder.decode(filename=filename, payload=content)
            except Exception:
                if self._config.production_profile:
                    raise
        result, rerun_stage = self._maybe_rerun_low_confidence(
            result,
            filename=filename,
            content=content,
            language=language,
            decoded_audio=decoded_audio,
            context=context,
            policy=policy,
        )
        if rerun_stage:
            stages.append(rerun_stage)

        result = self._ensure_metadata(result, pipeline=pipeline, stages=tuple(stages))
        return self._finalize_execution(
            result,
            filename=filename,
            content=content,
            language=language,
            context=context,
            decoded_audio=decoded_audio,
            policy=policy,
        )

    def _select_pipeline_backend(self, pipeline: str, *, policy: VoiceExecutionPolicy) -> VoiceBackend:
        if policy.source == "hub_context":
            return self._backend_for_id(policy.primary_backend)
        if pipeline == "oldschool_light":
            return self._backend_for_id(self._config.asr_backend)
        if pipeline == "whisper_cpp":
            return self._backend_for_id("whisper_cpp")
        if pipeline in {"meeting", "confidence_rerun", "custom", "realtime_streaming"}:
            return self._backend_for_id(self._config.asr_backend)
        return self._backend

    def _backend_for_id(self, backend_id: str) -> VoiceBackend:
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

    def _transcribe_parallel(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
        policy: VoiceExecutionPolicy,
        decoded_audio: DecodedPcmAudio | None,
    ) -> TranscriptionResult:
        backend_ids = tuple(dict.fromkeys((policy.primary_backend, *policy.secondary_backends)))
        backends = {backend_id: self._backend_for_id(backend_id) for backend_id in backend_ids}
        variants = self._audio_variants(
            filename=filename,
            content=content,
            decoded_audio=decoded_audio,
            policy=policy,
        )
        source_audio_digest = f"audio-lineage:{uuid.uuid4().hex}"
        candidates: list[TranscriptionCandidate] = []
        root_candidate_ids: dict[str, str] = {}
        remaining = policy.max_candidate_count
        shared_deadline = time.monotonic() + policy.candidate_deadline_sec
        for variant in variants:
            if variant.duplicate_of or remaining <= 0:
                continue
            remaining_deadline = shared_deadline - time.monotonic()
            if remaining_deadline <= 0:
                break
            selected_ids = backend_ids[:remaining]
            if variant.profile != "original":
                selected_ids = tuple(item for item in selected_ids if item in root_candidate_ids)
            if not selected_ids:
                continue
            variant_candidates = self._candidate_executor.execute(
                {backend_id: backends[backend_id] for backend_id in selected_ids},
                filename=f"{variant.profile}-{filename}",
                content=variant.content,
                language=language,
                policy=CandidateExecutionPolicy(
                    max_parallel_backends=policy.max_parallel_backends,
                    deadline_seconds=remaining_deadline,
                    audio_variant_id=variant.variant_id,
                    source_audio_id=source_audio_digest,
                    resource_budget=policy.resource_budget,
                    audio_duration_ms=decoded_audio.duration_ms if decoded_audio else 0,
                ),
                context=context,
            )
            for candidate in variant_candidates:
                if variant.profile == "original":
                    root_candidate_ids[candidate.backend] = candidate.candidate_id
                lineage_id = root_candidate_ids.get(candidate.backend, candidate.candidate_id)
                candidates.append(
                    replace(
                        candidate,
                        source_audio_digest=source_audio_digest,
                        lineage_id=lineage_id,
                        parent_candidate_ids=() if variant.profile == "original" else (lineage_id,),
                        provenance={
                            **dict(candidate.provenance),
                            "audio_variant": variant.metadata,
                            "audio_variant_profile": variant.profile,
                            **({"lineage_relation": "audio_variant"} if variant.profile != "original" else {}),
                        },
                    )
                )
            remaining -= len(variant_candidates)
        candidates_tuple = tuple(sorted(candidates, key=lambda item: (item.backend, item.audio_variant_id)))
        lineage_validation = self._lineage_validator.validate(candidates_tuple)
        outcome = self._fusion_service.fuse(candidates_tuple)
        strategy = policy.recognition_strategy
        trace = {**dict(outcome.result.decision_trace), "result_hash": outcome.result_hash, "execution": "parallel"}
        trace["audio_variants"] = [variant.metadata for variant in variants]
        trace["lineage_validation"] = {
            "valid": True,
            "root_count": len(lineage_validation.lineage_roots),
            "child_count": lineage_validation.child_count,
        }
        return replace(
            outcome.result,
            pipeline=self._config.transcription_pipeline,
            fusion_strategy=strategy,
            decision_trace=trace,
            stages=tuple(
                [
                    *outcome.result.stages,
                    {
                        "stage": "audio_enhancement",
                        "profiles": [variant.profile for variant in variants],
                        "executed_variants": sum(not variant.duplicate_of for variant in variants),
                        "duplicate_variants": sum(bool(variant.duplicate_of) for variant in variants),
                    },
                    {"stage": "candidate_execution", "mode": "parallel", "count": len(candidates_tuple)},
                    {"stage": "fusion", "strategy": strategy},
                ]
            ),
        )

    def _transcribe_classic_then_correct(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
        policy: VoiceExecutionPolicy,
        decoded_audio: DecodedPcmAudio | None,
    ) -> TranscriptionResult:
        shared_deadline = time.monotonic() + policy.candidate_deadline_sec
        source_audio_id = f"audio-lineage:{uuid.uuid4().hex}"
        classic_backend_id = policy.primary_backend
        classic_backend = self._backend_for_id(classic_backend_id)
        classic_candidate, classic_raw_result = self._execute_bounded_candidate(
            backend_id=classic_backend_id,
            backend=classic_backend,
            filename=filename,
            content=content,
            language=language,
            context=context,
            policy=policy,
            deadline_seconds=max(0.001, shared_deadline - time.monotonic()),
            audio_duration_ms=decoded_audio.duration_ms if decoded_audio else 0,
            source_audio_id=source_audio_id,
        )
        if classic_candidate.status != "succeeded":
            error = classic_candidate.error
            if error is not None:
                raise VoiceRuntimeError(error.code, error.message, error.retriable)
            raise RuntimeError("classic voice backend failed")
        classic_result = classic_raw_result or self._result_from_candidate(classic_candidate)
        classic_id = classic_candidate.candidate_id
        candidates: list[TranscriptionCandidate] = [classic_candidate]
        secondary_id = next(iter(policy.secondary_backends), None)
        if secondary_id:
            secondary = self._backend_for_id(secondary_id)
            classic_context = replace(
                context or VoiceRecognitionContext(),
                classic_transcript=classic_result.text,
                classic_words=tuple(word.as_dict() for segment in classic_result.segments for word in segment.words),
                language_hint=language or (context.language_hint if context else None),
            )
            remaining_deadline = shared_deadline - time.monotonic()
            secondary_candidate, _secondary_raw_result = self._execute_bounded_candidate(
                backend_id=secondary_id,
                backend=secondary,
                filename=filename,
                content=content,
                language=language,
                context=classic_context,
                policy=policy,
                deadline_seconds=max(0.001, remaining_deadline),
                audio_duration_ms=decoded_audio.duration_ms if decoded_audio else 0,
                source_audio_id=source_audio_id,
            )
            secondary_candidate = replace(
                secondary_candidate,
                parent_candidate_ids=(classic_id,),
                source_audio_digest=source_audio_id,
                lineage_id=classic_id,
                provenance={
                    **dict(secondary_candidate.provenance),
                    "lineage_relation": "context_derived",
                },
            )
            candidates.append(secondary_candidate)
        candidates_tuple = tuple(candidates)
        lineage_validation = self._lineage_validator.validate(candidates_tuple)
        if len(candidates_tuple) == 1 or candidates_tuple[-1].status != "succeeded":
            failure_code = (
                candidates_tuple[-1].error.code
                if len(candidates_tuple) > 1 and candidates_tuple[-1].error
                else "not_configured"
            )
            return replace(
                classic_result,
                pipeline=self._config.transcription_pipeline,
                warnings=tuple(
                    dict.fromkeys(
                        [
                            *classic_result.warnings,
                            *(
                                ["classic_corrector_failed"]
                                if len(candidates_tuple) > 1
                                else []
                            ),
                        ]
                    )
                ),
                candidates=candidates_tuple,
                selected_candidate_id=classic_id,
                fusion_strategy="classic_then_correct",
                decision_trace={
                    **dict(classic_result.decision_trace),
                    "execution": "sequential",
                    "classic_candidate_id": classic_id,
                    "classic_preserved": True,
                    "corrector_status": "failed" if len(candidates_tuple) > 1 else "not_configured",
                    "corrector_reason_code": failure_code,
                    "lineage_validation": {
                        "valid": True,
                        "root_count": len(lineage_validation.lineage_roots),
                        "child_count": lineage_validation.child_count,
                    },
                },
                stages=tuple(
                    [
                        *classic_result.stages,
                        {
                            "stage": "candidate_execution",
                            "mode": "sequential",
                            "count": len(candidates_tuple),
                        },
                        {
                            "stage": "fusion",
                            "strategy": "classic_then_correct",
                            "status": "classic_preserved",
                        },
                    ]
                ),
            )
        outcome = self._fusion_service.fuse(candidates_tuple)
        return replace(
            outcome.result,
            pipeline=self._config.transcription_pipeline,
            fusion_strategy="classic_then_correct",
            decision_trace={
                **dict(outcome.result.decision_trace),
                "result_hash": outcome.result_hash,
                "execution": "sequential",
                "classic_candidate_id": classic_id,
                "classic_preserved": outcome.result.selected_candidate_id == classic_id,
                "corrector_status": "succeeded",
                "lineage_validation": {
                    "valid": True,
                    "root_count": len(lineage_validation.lineage_roots),
                    "child_count": lineage_validation.child_count,
                },
            },
            stages=tuple(
                [
                    *outcome.result.stages,
                    {"stage": "candidate_execution", "mode": "sequential", "count": len(candidates)},
                    {"stage": "fusion", "strategy": "classic_then_correct"},
                ]
            ),
        )

    def _decode_at_trust_boundary(
        self,
        *,
        filename: str,
        content: bytes,
        policy: VoiceExecutionPolicy,
    ) -> DecodedPcmAudio | None:
        backend_ids = self._effective_backend_ids(policy)
        requires_decoded_audio = (
            any(backend_id != "mock" for backend_id in backend_ids)
            or len(policy.enhancement_variants) > 1
            or policy.diarization_backend == "pyannote"
            or policy.routing_strategy == "adaptive_local"
        )
        if not requires_decoded_audio:
            return None
        return self._decode_required(filename=filename, content=content)

    def _decode_required(self, *, filename: str, content: bytes) -> DecodedPcmAudio:
        try:
            return self._audio_decoder.decode(filename=filename, payload=content)
        except AudioDecodeError as exc:
            raise InvalidAudioError("audio input failed strict decode validation") from exc

    def _effective_backend_ids(self, policy: VoiceExecutionPolicy) -> tuple[str, ...]:
        if policy.source == "hub_context" or policy.recognition_strategy in {
            "parallel_compare",
            "parallel_fusion",
            "classic_then_correct",
        }:
            return tuple(dict.fromkeys((policy.primary_backend, *policy.secondary_backends)))
        if self._config.transcription_pipeline == "simple":
            return self._config.backend_fallback_order
        return (self._config.asr_backend,)

    def _audio_variants(
        self,
        *,
        filename: str,
        content: bytes,
        decoded_audio: DecodedPcmAudio | None,
        policy: VoiceExecutionPolicy,
    ) -> tuple[PreparedAudioVariant, ...]:
        if policy.enhancement_variants == ("original",):
            return (
                PreparedAudioVariant(
                    profile="original",
                    variant_id="original",
                    content=content,
                    metadata={
                        "variant_id": "original",
                        "label": "original",
                        "lineage": [],
                        "decode_boundary": "validated" if decoded_audio is not None else "synthetic_mock_fixture",
                    },
                ),
            )
        audio = decoded_audio or self._decode_required(filename=filename, content=content)
        return cast(
            tuple[PreparedAudioVariant, ...],
            prepare_enhancement_variants(audio, policy.enhancement_variants),
        )

    def _finalize_execution(
        self,
        result: TranscriptionResult,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
        decoded_audio: DecodedPcmAudio | None,
        policy: VoiceExecutionPolicy,
    ) -> TranscriptionResult:
        stages = list(result.stages)
        result, adaptive_stage = self._maybe_adaptive_rerun(
            result,
            filename=filename,
            content=content,
            language=language,
            context=context,
            decoded_audio=decoded_audio,
            policy=policy,
        )
        if adaptive_stage:
            stages.append(adaptive_stage)
        result, diarization_stage = self._maybe_diarize(
            result,
            decoded_audio=decoded_audio,
            policy=policy,
        )
        if diarization_stage:
            stages.append(diarization_stage)
        result, postprocess_stage = self._maybe_postprocess(result, policy=policy, context=context)
        if postprocess_stage:
            stages.append(postprocess_stage)
        result, correction_stage = self._maybe_apply_hub_correction(result, policy=policy)
        if correction_stage:
            stages.append(correction_stage)
        trace = dict(result.decision_trace)
        if policy.source == "hub_context":
            stages.append(
                {
                    "stage": "execution_policy",
                    "source": policy.source,
                    "recognition_strategy": policy.recognition_strategy,
                    "routing_strategy": policy.routing_strategy,
                    "correction_policy": policy.correction_policy,
                    "review_policy": policy.review_policy,
                    "primary_backend": policy.primary_backend,
                    "secondary_backends": list(policy.secondary_backends),
                    "max_parallel_backends": policy.max_parallel_backends,
                    "max_candidate_count": policy.max_candidate_count,
                    "candidate_deadline_sec": policy.candidate_deadline_sec,
                    "resource_budget": policy.resource_budget.as_dict(),
                    "adjustments": [dict(item) for item in policy.adjustments],
                }
            )
            trace["execution_policy"] = {
                "source": policy.source,
                "adjustments": [dict(item) for item in policy.adjustments],
            }
        return replace(
            result,
            stages=tuple(stages),
            decision_trace=trace,
        )

    def _maybe_adaptive_rerun(
        self,
        result: TranscriptionResult,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: VoiceRecognitionContext | None,
        decoded_audio: DecodedPcmAudio | None,
        policy: VoiceExecutionPolicy,
    ) -> tuple[TranscriptionResult, dict | None]:
        if policy.routing_strategy != "adaptive_local":
            return result, None
        if decoded_audio is None:
            return result.with_additional_warnings(["adaptive_routing_input_unavailable"]), {
                "stage": "adaptive_routing",
                "status": "skipped",
                "reason_codes": ["decoded_audio_unavailable"],
            }
        backend_ids = tuple(dict.fromkeys((policy.primary_backend, *policy.secondary_backends)))
        calibration = self._calibration_for_result(result)
        overall_confidence = calibration.calibrate(result.confidence) if calibration else result.confidence
        calibration_id = calibration.dataset_version if calibration else None
        regions = tuple(
            ConfidenceRegion(
                start_ms=segment.start_ms,
                end_ms=max(segment.start_ms + 1, segment.end_ms),
                confidence=calibration.calibrate(segment.confidence) if calibration else segment.confidence,
                calibration_id=calibration_id,
            )
            for segment in result.segments
            if segment.confidence is not None and segment.end_ms > segment.start_ms
        )
        devices = ("cuda", "cpu") if self._config.device == "auto" else (self._config.device,)
        routing_policy = RoutingPolicyEnvelope(
            allowed_backends=backend_ids,
            preferred_backends=backend_ids,
            allowed_devices=devices,
            max_candidate_count=min(policy.max_parallel_backends, len(backend_ids)),
            max_total_latency_ms=min(
                self._config.adaptive_max_total_latency_ms,
                max(1, round(policy.candidate_deadline_sec * 1000)),
            ),
            max_regional_rerun_ms=self._config.adaptive_max_regional_rerun_ms,
            confidence_threshold=policy.confidence_threshold,
        )
        capabilities = tuple(
            BackendRoute(
                backend_id=backend_id,
                local_execution=True,
                available=True,
                supported_devices=devices,
                fixed_latency_ms=0,
                latency_per_audio_second_ms=1000,
                supports_regional_input=backend_id != policy.primary_backend,
            )
            for backend_id in backend_ids
        )
        decision = self._adaptive_router.decide(
            policy=routing_policy,
            measurements=RoutingMeasurements(
                audio_duration_ms=decoded_audio.duration_ms,
                overall_confidence=overall_confidence,
                overall_calibration_id=calibration_id,
                confidence_regions=regions,
                available_devices=devices,
            ),
            capabilities=capabilities,
        )
        replacements: dict[str, tuple[TranscriptionSegment, ...]] = {}
        rerun_outcomes: list[dict[str, object]] = []
        shared_deadline = time.monotonic() + min(
            policy.candidate_deadline_sec,
            self._config.adaptive_max_total_latency_ms / 1000.0,
        )
        for region in decision.rerun_regions:
            try:
                remaining_deadline = shared_deadline - time.monotonic()
                if remaining_deadline <= 0:
                    rerun_outcomes.append(
                        {"region_id": region.region_id, "status": "timeout"}
                    )
                    break
                sliced = decoded_audio.slice_ms(region.start_ms, region.end_ms)
                candidate, raw_rerun = self._execute_bounded_candidate(
                    backend_id=region.backend_id,
                    backend=self._backend_for_id(region.backend_id),
                    filename=f"adaptive-{region.start_ms}-{region.end_ms}-{filename}",
                    content=sliced.to_wav_bytes(),
                    language=language,
                    context=context,
                    policy=policy,
                    deadline_seconds=remaining_deadline,
                    audio_duration_ms=sliced.duration_ms,
                )
                if candidate.status != "succeeded":
                    rerun_outcomes.append(
                        {
                            "region_id": region.region_id,
                            "status": "failed",
                            "reason_code": candidate.error.code
                            if candidate.error
                            else "backend_error",
                        }
                    )
                    continue
                rerun = raw_rerun or self._result_from_candidate(candidate)
                text = rerun.text.strip()
                if not text:
                    rerun_outcomes.append(
                        {"region_id": region.region_id, "status": "empty"}
                    )
                    continue
                replacements[region.region_id] = (
                    self._regional_replacement(rerun, region.start_ms, region.end_ms, region.backend_id),
                )
                rerun_outcomes.append(
                    {"region_id": region.region_id, "status": "applied"}
                )
            except Exception:
                rerun_outcomes.append(
                    {
                        "region_id": region.region_id,
                        "status": "failed",
                        "reason_code": "backend_error",
                    }
                )
                continue
        merged_segments = merge_regional_segments(
            baseline=result.segments,
            regions=decision.rerun_regions,
            replacements=replacements,
        )
        updated = result
        if replacements:
            confidences = [item.confidence for item in merged_segments if item.confidence is not None]
            applied_backend = next(
                region.backend_id for region in decision.rerun_regions if region.region_id in replacements
            )
            updated = replace(
                result,
                text=" ".join(item.text for item in merged_segments if item.text).strip(),
                segments=merged_segments,
                confidence=sum(confidences) / len(confidences) if confidences else result.confidence,
                rerun_backend=applied_backend,
                warnings=tuple([*result.warnings, "adaptive_regional_rerun_applied"]),
            )
        trace = {
            **dict(updated.decision_trace),
            "adaptive_routing": {
                "reason_codes": list(decision.reason_codes),
                "selected_backends": [item.backend_id for item in decision.selected_backends],
                "skipped_backends": [
                    {"backend": item.backend_id, "reason_code": item.reason_code} for item in decision.skipped_backends
                ],
                "rerun_regions": [
                    {"region_id": item.region_id, "start_ms": item.start_ms, "end_ms": item.end_ms}
                    for item in decision.rerun_regions
                ],
                "applied_region_count": len(replacements),
                "rerun_outcomes": rerun_outcomes,
            },
        }
        return replace(updated, decision_trace=trace), {
            "stage": "adaptive_routing",
            "status": "applied" if replacements else "evaluated",
            "reason_codes": list(decision.reason_codes),
            "rerun_count": len(replacements),
            "estimated_total_latency_ms": decision.estimated_total_latency_ms,
        }

    def _calibration_for_result(self, result: TranscriptionResult) -> CalibrationProfile | None:
        candidate: TranscriptionCandidate | None = None
        if result.selected_candidate_id:
            candidate = next(
                (item for item in result.candidates if item.candidate_id == result.selected_candidate_id),
                None,
            )
        if candidate is None:
            candidate = TranscriptionCandidate.from_result(
                candidate_id="adaptive-routing-baseline",
                backend=result.raw_backend or "unknown",
                result=result,
            )
        profile = self._candidate_scorer.calibration_profile(candidate)
        return profile if profile is not None and profile.comparable else None

    @staticmethod
    def _regional_replacement(
        result: TranscriptionResult,
        start_ms: int,
        end_ms: int,
        backend_id: str,
    ) -> TranscriptionSegment:
        words = tuple(
            TranscriptionWord(
                start_ms=max(start_ms, min(end_ms, start_ms + word.start_ms)),
                end_ms=max(start_ms, min(end_ms, start_ms + word.end_ms)),
                text=word.text,
                confidence=word.confidence,
                candidate_id=word.candidate_id,
            )
            for segment in result.segments
            for word in segment.words
            if word.text
        )
        return TranscriptionSegment(
            start_ms=start_ms,
            end_ms=end_ms,
            text=result.text.strip(),
            confidence=result.confidence,
            backend=backend_id,
            warnings=("adaptive_regional_rerun_applied",),
            words=words,
        )

    def _maybe_apply_hub_correction(
        self,
        result: TranscriptionResult,
        *,
        policy: VoiceExecutionPolicy,
    ) -> tuple[TranscriptionResult, dict | None]:
        if policy.correction_policy in {"restricted_choice", "local_schema_corrector"}:
            policy_name = "restricted_choice" if policy.correction_policy == "restricted_choice" else "generative_local"
            return result, {
                "stage": "correction_boundary",
                "policy": policy_name,
                "status": "hub_postprocessing_required",
                "runtime_worker_call": False,
                "no_generation": policy.correction_policy == "restricted_choice",
            }
        return result, None

    def _merge_results(
        self,
        *,
        results: list[TranscriptionResult],
        filename: str,
        fallback_language: str | None,
    ) -> TranscriptionResult:
        if not results:
            return TranscriptionResult(
                text="",
                language=fallback_language or "und",
                model=self._config.model,
                warnings=("pipeline_no_segments",),
            )
        warnings: list[str] = []
        segments: list[TranscriptionSegment] = []
        offset_ms = 0
        for result in results:
            warnings.extend(result.warnings)
            if result.segments:
                segments.extend(result.segments)
            else:
                duration = result.duration_ms or max(50, len(result.text) * 2)
                segments.append(
                    TranscriptionSegment(
                        start_ms=offset_ms,
                        end_ms=offset_ms + duration,
                        text=result.text,
                        confidence=result.confidence,
                        backend=result.raw_backend or result.model,
                    )
                )
            offset_ms = max(offset_ms, result.duration_ms or 0)
        text = " ".join(segment.text for segment in segments if segment.text).strip()
        confidences = [segment.confidence for segment in segments if segment.confidence is not None]
        return TranscriptionResult(
            text=text or f"transcript ({filename or 'audio'})",
            language=results[0].language or fallback_language or "und",
            duration_ms=max((segment.end_ms for segment in segments), default=results[0].duration_ms),
            model=results[0].model or self._config.model,
            warnings=tuple(warnings),
            segments=tuple(segments),
            confidence=(sum(confidences) / len(confidences)) if confidences else results[0].confidence,
            raw_backend=results[0].raw_backend or results[0].model,
        )

    def _maybe_rerun_low_confidence(
        self,
        result: TranscriptionResult,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        decoded_audio: DecodedPcmAudio | None,
        context: VoiceRecognitionContext | None,
        policy: VoiceExecutionPolicy,
    ) -> tuple[TranscriptionResult, dict | None]:
        enabled = self._config.confidence_rerun_enabled or self._config.transcription_pipeline == "confidence_rerun"
        if not enabled or self._config.rerun_max_segments <= 0:
            return result, None
        calibration = self._calibration_for_result(result)
        threshold = (
            calibration.minimum_confidence
            if calibration and calibration.minimum_confidence is not None
            else self._config.confidence_threshold
        )
        low = [
            segment
            for segment in result.segments
            if segment.confidence is not None and segment.confidence < threshold
        ][: self._config.rerun_max_segments]
        if not low:
            return result, {"stage": "confidence_rerun", "backend": self._config.rerun_backend, "rerun_count": 0}
        if decoded_audio is None:
            return result.with_additional_warnings(["confidence_rerun_pcm_unavailable"]), {
                "stage": "confidence_rerun",
                "backend": self._config.rerun_backend,
                "rerun_count": 0,
                "scope": "low_confidence_pcm_regions",
                "status": "skipped",
                "reason_code": "decoded_pcm_unavailable",
                "full_audio_fallback": False,
            }

        del content
        rerun_backend = self._backend_for_id(self._config.rerun_backend)
        remaining_audio_ms = min(
            self._config.rerun_max_audio_ms,
            policy.resource_budget.max_audio_ms,
        )
        shared_deadline = time.monotonic() + policy.candidate_deadline_sec
        regions = []
        replacements: dict[str, tuple[TranscriptionSegment, ...]] = {}
        outcomes: list[dict[str, object]] = []
        for index, segment in enumerate(low):
            duration_ms = max(0, segment.end_ms - segment.start_ms)
            region_id = f"confidence-rerun-{index:04d}-{segment.start_ms}-{segment.end_ms}"
            if duration_ms <= 0 or duration_ms > remaining_audio_ms:
                outcomes.append(
                    {"region_id": region_id, "status": "budget_skipped"}
                )
                continue
            remaining_deadline = shared_deadline - time.monotonic()
            if remaining_deadline <= 0:
                outcomes.append({"region_id": region_id, "status": "timeout"})
                break
            relative_start = max(
                0,
                segment.start_ms - decoded_audio.timeline_start_ms,
            )
            relative_end = max(
                relative_start,
                segment.end_ms - decoded_audio.timeline_start_ms,
            )
            sliced = decoded_audio.slice_ms(relative_start, relative_end)
            if sliced.duration_ms <= 0:
                outcomes.append({"region_id": region_id, "status": "empty_slice"})
                continue
            region = RerunRegion(
                region_id,
                segment.start_ms,
                segment.end_ms,
                self._config.rerun_backend,
                self._config.device,
            )
            regions.append(region)
            candidate, raw_rerun = self._execute_bounded_candidate(
                backend_id=self._config.rerun_backend,
                backend=rerun_backend,
                filename=f"rerun-{segment.start_ms}-{segment.end_ms}.wav",
                content=sliced.to_wav_bytes(),
                language=language,
                context=context,
                policy=policy,
                deadline_seconds=remaining_deadline,
                audio_duration_ms=sliced.duration_ms,
            )
            remaining_audio_ms -= sliced.duration_ms
            if candidate.status != "succeeded" or not candidate.text.strip():
                outcomes.append(
                    {
                        "region_id": region_id,
                        "status": "failed",
                        "reason_code": candidate.error.code
                        if candidate.error
                        else "empty_result",
                    }
                )
                continue
            rerun = raw_rerun or self._result_from_candidate(candidate)
            replacements[region_id] = (
                self._regional_replacement(
                    rerun,
                    segment.start_ms,
                    segment.end_ms,
                    self._config.rerun_backend,
                ),
            )
            outcomes.append({"region_id": region_id, "status": "applied"})

        merged_segments = merge_regional_segments(
            baseline=result.segments,
            regions=tuple(regions),
            replacements=replacements,
        )
        trace = {
            **dict(result.decision_trace),
            "confidence_rerun": {
                "threshold": threshold,
                "threshold_source": calibration.dataset_version
                if calibration
                else "runtime_config",
                "outcomes": outcomes,
                "full_audio_fallback": False,
                "max_segments": self._config.rerun_max_segments,
                "max_audio_ms": min(
                    self._config.rerun_max_audio_ms,
                    policy.resource_budget.max_audio_ms,
                ),
            },
        }
        if not replacements:
            warning = "confidence_rerun_failed" if outcomes else "confidence_rerun_skipped"
            return replace(
                result,
                warnings=tuple([*result.warnings, warning]),
                decision_trace=trace,
            ), {
                "stage": "confidence_rerun",
                "backend": self._config.rerun_backend,
                "rerun_count": 0,
                "scope": "low_confidence_pcm_regions",
                "full_audio_fallback": False,
                "outcomes": outcomes,
            }
        confidences = [
            item.confidence for item in merged_segments if item.confidence is not None
        ]
        return replace(
            result,
            text=" ".join(item.text for item in merged_segments if item.text).strip(),
            segments=merged_segments,
            confidence=sum(confidences) / len(confidences)
            if confidences
            else result.confidence,
            warnings=tuple([*result.warnings, "confidence_rerun_applied"]),
            rerun_backend=self._config.rerun_backend,
            decision_trace=trace,
        ), {
            "stage": "confidence_rerun",
            "backend": self._config.rerun_backend,
            "rerun_count": len(replacements),
            "scope": "low_confidence_pcm_regions",
            "full_audio_fallback": False,
            "outcomes": outcomes,
        }

    def _maybe_diarize(
        self,
        result: TranscriptionResult,
        *,
        decoded_audio: DecodedPcmAudio | None,
        policy: VoiceExecutionPolicy,
    ) -> tuple[TranscriptionResult, dict | None]:
        if policy.diarization_backend == "pyannote":
            if decoded_audio is None:
                return result.with_additional_warnings(["diarization_input_unavailable"]), {
                    "stage": "diarization",
                    "backend": "pyannote",
                    "status": "skipped",
                    "reason_code": "decoded_audio_unavailable",
                }
            adapter = self._resolve_diarization_adapter()
            if adapter is None:
                return result.with_additional_warnings(["diarization_unavailable"]), {
                    "stage": "diarization",
                    "backend": "pyannote",
                    "status": "skipped",
                    "reason_code": "adapter_unavailable",
                }
            outcome = SafeDiarizationProcessor(adapter).process(audio=decoded_audio, segments=result.segments)
            warnings = list(result.warnings)
            if outcome.status != "succeeded":
                warnings.append("diarization_unavailable")
            return replace(result, segments=outcome.segments, warnings=tuple(warnings)), {
                "stage": "diarization",
                "backend": outcome.adapter_id,
                "status": outcome.status,
                "reason_code": outcome.reason_code,
                "segment_count": len(result.segments),
            }
        processor = build_diarization_processor(policy.diarization_backend)
        if processor is None:
            return result, None
        return replace(result, segments=processor.assign(result.segments)), {
            "stage": "diarization",
            "backend": processor.name(),
            "segment_count": len(result.segments),
        }

    def _maybe_postprocess(
        self,
        result: TranscriptionResult,
        *,
        policy: VoiceExecutionPolicy,
        context: VoiceRecognitionContext | None,
    ) -> tuple[TranscriptionResult, dict | None]:
        glossary = Glossary.load(self._config.glossary_path)
        personalization_metadata: dict[str, object] = {}
        if context is not None and (context.substitutions or context.preferences):
            weights = dict(context.personalization_weights)
            replacements = dict(glossary.replacements)
            if weights.get("substitution", 1.0) > 0:
                replacements.update(
                    {source.casefold(): target for source, target in context.substitutions}
                )
            if weights.get("preference", 1.0) > 0:
                for source, target in context.preferences:
                    replacements.setdefault(source.casefold(), target)
            glossary = Glossary(replacements=replacements, warnings=glossary.warnings)
            personalization_metadata = {
                "personalization_snapshot_version": context.snapshot_version,
                "personalization_consent_reference": context.consent_reference,
                "personalization_consent_version": context.consent_version,
                "personalization_substitution_count": len(context.substitutions),
                "personalization_preference_count": len(context.preferences),
                "personalization_weights": weights,
            }
        backend = (
            "rules"
            if policy.source == "hub_context" and policy.correction_policy == "rules"
            else self._config.postprocess_backend
            if policy.source == "runtime_default"
            else "none"
        )
        processor = build_postprocessor(backend, glossary=glossary)
        if processor is None:
            if glossary.warnings:
                return result.with_additional_warnings(list(glossary.warnings)), None
            return result, None
        inputs = tuple(segment.text for segment in result.segments) or (result.text,)
        processed_parts = tuple(processor.process(text) for text in inputs)
        segment_records = [
            {
                "segment_index": index if result.segments else None,
                "start_ms": result.segments[index].start_ms if result.segments else None,
                "end_ms": result.segments[index].end_ms if result.segments else None,
                "original_text": processed.original_text,
                "applied_text": processed.text,
                "proposed_text": processed.proposed_text,
                "review_required": processed.review_required,
                "conflict_reason": processed.conflict_reason,
                "edits": [edit.as_dict() for edit in processed.edits],
            }
            for index, processed in enumerate(processed_parts)
        ]
        processed_segments = tuple(
            replace(segment, text=processed_parts[index].text)
            for index, segment in enumerate(result.segments)
        )
        processed_text = (
            " ".join(segment.text for segment in processed_segments if segment.text).strip()
            if processed_segments
            else processed_parts[0].text
        )
        postprocess_trace = {
            "processor": processor.name(),
            "segments": segment_records,
            "review_required": any(item.review_required for item in processed_parts),
        }
        trace = {**dict(result.decision_trace), "postprocessing": postprocess_trace}
        warnings = tuple(
            dict.fromkeys([*result.warnings, *(warning for item in processed_parts for warning in item.warnings)])
        )
        updated = replace(
            result,
            text=processed_text,
            segments=processed_segments or result.segments,
            warnings=warnings,
            decision_trace=trace,
        )
        return updated, {
            "stage": "postprocess",
            "backend": processor.name(),
            "changed": any(processed.changed for processed in processed_parts),
            "review_required": postprocess_trace["review_required"],
            "segments": segment_records,
            "llm_used": processor.name() == "llm",
            **personalization_metadata,
            **glossary.as_stage_metadata(),
        }

    def _resolve_diarization_adapter(self) -> LocalDiarizationAdapter | None:
        if self._diarization_adapter_initialized:
            return self._diarization_adapter
        self._diarization_adapter_initialized = True
        if not self._config.pyannote_manifest_path or not self._config.diarization_model_root:
            return None
        try:
            manifest = load_offline_diarization_manifest(self._config.pyannote_manifest_path)
            self._diarization_adapter = PyannoteDiarizationAdapter(
                manifest=manifest,
                allowed_model_roots=(Path(self._config.diarization_model_root),),
            )
        except Exception:
            self._diarization_adapter = None
        return self._diarization_adapter

    @staticmethod
    def _shift_result(result: TranscriptionResult, *, offset_ms: int) -> TranscriptionResult:
        if offset_ms == 0:
            return result
        shifted_segments = tuple(
            replace(
                segment,
                start_ms=segment.start_ms + offset_ms,
                end_ms=segment.end_ms + offset_ms,
                words=tuple(
                    replace(word, start_ms=word.start_ms + offset_ms, end_ms=word.end_ms + offset_ms)
                    for word in segment.words
                ),
            )
            for segment in result.segments
        )
        return replace(
            result,
            duration_ms=(result.duration_ms + offset_ms) if result.duration_ms is not None else None,
            segments=shifted_segments,
        )

    def _execute_bounded_candidate(
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
                    candidate_id=_stable_candidate_id(
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

    def _transcribe_bounded_result(
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
        candidate, raw_result = self._execute_bounded_candidate(
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
            return raw_result or self._result_from_candidate(candidate)
        error = candidate.error
        if error is not None:
            raise VoiceRuntimeError(error.code, error.message, error.retriable)
        raise RuntimeError("voice backend execution failed")

    @staticmethod
    def _result_from_candidate(candidate: TranscriptionCandidate) -> TranscriptionResult:
        return TranscriptionResult(
            text=candidate.text,
            language=candidate.language,
            duration_ms=candidate.duration_ms,
            model=candidate.model,
            warnings=candidate.warnings,
            segments=candidate.segments,
            confidence=candidate.confidence,
            raw_backend=candidate.backend,
            provenance=dict(candidate.provenance),
        )

    @staticmethod
    def _ensure_metadata(
        result: TranscriptionResult,
        *,
        pipeline: str,
        stages: tuple[dict, ...],
    ) -> TranscriptionResult:
        segments = result.segments
        if not segments and result.text:
            segments = (
                TranscriptionSegment(
                    start_ms=0,
                    end_ms=result.duration_ms or max(50, len(result.text) * 2),
                    text=result.text,
                    confidence=result.confidence,
                    backend=result.raw_backend or result.model,
                ),
            )
        confidences = [segment.confidence for segment in segments if segment.confidence is not None]
        confidence = result.confidence
        if confidence is None and confidences:
            confidence = sum(confidences) / len(confidences)
        return replace(
            result,
            segments=segments,
            pipeline=result.pipeline or pipeline,
            confidence=confidence,
            raw_backend=result.raw_backend or result.model,
            stages=tuple([*result.stages, *stages]),
        )


def _stable_candidate_id(backend_id: str, source_audio_id: str, variant: str) -> str:
    digest = hashlib.sha256(
        backend_id.encode("utf-8")
        + b"\0"
        + variant.encode("utf-8")
        + b"\0"
        + source_audio_id.encode("utf-8")
    ).hexdigest()
    return f"candidate-{backend_id}-{digest[:16]}"
