from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import cast

from .backends.base import (
    TranscriptionCandidate,
    TranscriptionResult,
    VoiceBackend,
    VoiceBackendResolver,
)
from .config import VoiceRuntimeConfig
from .context import VoiceRecognitionContext
from .diarization_adapters import LocalDiarizationAdapter
from .errors import InvalidAudioError, VoiceRuntimeError
from .execution_policy import VoiceExecutionPolicy
from .fusion import (
    CandidateLineageValidator,
    CandidateScorer,
    DeterministicFusionService,
    load_calibration_profiles,
)
from .model_manifest import VoiceModelCatalog
from .parallel import CandidateExecutionPolicy, ParallelCandidateExecutor
from .pipeline_adaptive_rerun import AdaptiveRegionalRerunStage
from .pipeline_confidence_rerun import ConfidenceRerunStage
from .pipeline_diarization import DiarizationStage
from .pipeline_execution import BoundedCandidateRunner, LegacyBackendResolver, PipelineBackendSelector
from .pipeline_extensions import PreparedAudioVariant, prepare_enhancement_variants
from .pipeline_postprocessing import TranscriptPostprocessingStage, apply_hub_correction_boundary
from .pipeline_results import (
    ensure_metadata,
    merge_results,
    result_from_candidate,
    shift_result,
)
from .preprocessing import (
    AudioDecodeLimits,
    DecodedPcmAudio,
    SafeAudioDecoder,
    build_pcm_vad_processor,
    build_vad_processor,
)
from .preprocessing.audio_decode import AudioDecodeError
from .resources import ResourceAdmissionController, resource_budget_from_config
from .routing import AdaptiveLocalRouter
from .source_correction import (
    SourceCorrectionPort,
    SourceCorrectionRequest,
    SourceCorrectionResult,
    SourceCorrectionService,
)


class TranscriptionPipeline:
    """Configurable Voice Runtime transcription orchestrator.

    The pipeline selects the recognition strategy and composes its stages;
    backend resolution, bounded candidate execution, regional reruns,
    diarization and post-processing are injected collaborators (SRP/DIP).
    """

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
        backend_selector: PipelineBackendSelector | None = None,
        candidate_runner: BoundedCandidateRunner | None = None,
        adaptive_rerun_stage: AdaptiveRegionalRerunStage | None = None,
        confidence_rerun_stage: ConfidenceRerunStage | None = None,
        diarization_stage: DiarizationStage | None = None,
        postprocessing_stage: TranscriptPostprocessingStage | None = None,
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
        self._adaptive_router = adaptive_router or AdaptiveLocalRouter()
        self._lineage_validator = lineage_validator or CandidateLineageValidator()
        self._source_correction = source_correction or SourceCorrectionService()
        # Kept for callers/tests that inspect the injected long-lived resolver.
        self._backend_resolver = backend_resolver
        self._backend_selector = backend_selector or PipelineBackendSelector(
            config=config,
            default_backend=backend,
            backend_resolver=backend_resolver,
        )
        self._candidate_runner = candidate_runner or BoundedCandidateRunner(
            candidate_executor=self._candidate_executor,
        )
        self._adaptive_rerun = adaptive_rerun_stage or AdaptiveRegionalRerunStage(
            config=config,
            backend_selector=self._backend_selector,
            candidate_runner=self._candidate_runner,
            candidate_scorer=self._candidate_scorer,
            adaptive_router=self._adaptive_router,
        )
        self._confidence_rerun = confidence_rerun_stage or ConfidenceRerunStage(
            config=config,
            backend_selector=self._backend_selector,
            candidate_runner=self._candidate_runner,
            candidate_scorer=self._candidate_scorer,
        )
        self._diarization = diarization_stage or DiarizationStage(
            config=config,
            adapter=diarization_adapter,
        )
        self._postprocessing = postprocessing_stage or TranscriptPostprocessingStage(config=config)

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
            adapter = self._diarization.resolve_adapter()
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
                self._backend_selector.backend_for_id(policy.primary_backend)
                if policy.source == "hub_context"
                else self._backend
            )
            result = self._candidate_runner.transcribe(
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
            result = ensure_metadata(
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

        asr_backend = self._backend_selector.select_for_pipeline(pipeline, policy=policy)
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
                shift_result(
                    self._candidate_runner.transcribe(
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
                self._candidate_runner.transcribe(
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
        result = merge_results(
            results=results,
            filename=filename,
            fallback_language=language,
            default_model=self._config.model,
        )
        stages.append(
            {"stage": "asr", "backend": result.raw_backend or asr_backend.name(), "segment_count": len(result.segments)}
        )

        if decoded_audio is None and (self._config.confidence_rerun_enabled or pipeline == "confidence_rerun"):
            try:
                decoded_audio = self._audio_decoder.decode(filename=filename, payload=content)
            except Exception:
                if self._config.production_profile:
                    raise
        result, rerun_stage = self._confidence_rerun.apply(
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

        result = ensure_metadata(result, pipeline=pipeline, stages=tuple(stages))
        return self._finalize_execution(
            result,
            filename=filename,
            content=content,
            language=language,
            context=context,
            decoded_audio=decoded_audio,
            policy=policy,
        )

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
        backends = {backend_id: self._backend_selector.backend_for_id(backend_id) for backend_id in backend_ids}
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
        classic_backend = self._backend_selector.backend_for_id(classic_backend_id)
        classic_candidate, classic_raw_result = self._candidate_runner.execute(
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
        classic_result = classic_raw_result or result_from_candidate(classic_candidate)
        classic_id = classic_candidate.candidate_id
        candidates: list[TranscriptionCandidate] = [classic_candidate]
        secondary_id = next(iter(policy.secondary_backends), None)
        if secondary_id:
            secondary = self._backend_selector.backend_for_id(secondary_id)
            classic_context = replace(
                context or VoiceRecognitionContext(),
                classic_transcript=classic_result.text,
                classic_words=tuple(word.as_dict() for segment in classic_result.segments for word in segment.words),
                language_hint=language or (context.language_hint if context else None),
            )
            remaining_deadline = shared_deadline - time.monotonic()
            secondary_candidate, _secondary_raw_result = self._candidate_runner.execute(
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
        result, adaptive_stage = self._adaptive_rerun.apply(
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
        result, diarization_stage = self._diarization.apply(
            result,
            decoded_audio=decoded_audio,
            policy=policy,
        )
        if diarization_stage:
            stages.append(diarization_stage)
        result, postprocess_stage = self._postprocessing.apply(result, policy=policy, context=context)
        if postprocess_stage:
            stages.append(postprocess_stage)
        result, correction_stage = apply_hub_correction_boundary(result, policy=policy)
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
