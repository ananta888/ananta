"""Adaptive local regional rerun stage of the transcription pipeline.

The stage asks the local router which low-confidence regions deserve a
bounded rerun on another already-allowed backend and merges the accepted
regional replacements. It executes locally only and never delegates work.
"""

from __future__ import annotations

import time
from dataclasses import replace

from .backends.base import TranscriptionResult, TranscriptionSegment
from .config import VoiceRuntimeConfig
from .context import VoiceRecognitionContext
from .execution_policy import VoiceExecutionPolicy
from .fusion import CandidateScorer
from .pipeline_execution import BoundedCandidateRunner, PipelineBackendSelector
from .pipeline_results import calibration_for_result, regional_replacement, result_from_candidate
from .preprocessing import DecodedPcmAudio
from .routing import (
    AdaptiveLocalRouter,
    BackendRoute,
    ConfidenceRegion,
    RoutingMeasurements,
    RoutingPolicyEnvelope,
    merge_regional_segments,
)


class AdaptiveRegionalRerunStage:
    """Apply Hub-policy ``adaptive_local`` routing to a finished result."""

    def __init__(
        self,
        *,
        config: VoiceRuntimeConfig,
        backend_selector: PipelineBackendSelector,
        candidate_runner: BoundedCandidateRunner,
        candidate_scorer: CandidateScorer,
        adaptive_router: AdaptiveLocalRouter,
    ) -> None:
        self._config = config
        self._backend_selector = backend_selector
        self._candidate_runner = candidate_runner
        self._candidate_scorer = candidate_scorer
        self._adaptive_router = adaptive_router

    def apply(
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
        calibration = calibration_for_result(self._candidate_scorer, result)
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
                candidate, raw_rerun = self._candidate_runner.execute(
                    backend_id=region.backend_id,
                    backend=self._backend_selector.backend_for_id(region.backend_id),
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
                rerun = raw_rerun or result_from_candidate(candidate)
                text = rerun.text.strip()
                if not text:
                    rerun_outcomes.append(
                        {"region_id": region.region_id, "status": "empty"}
                    )
                    continue
                replacements[region.region_id] = (
                    regional_replacement(rerun, region.start_ms, region.end_ms, region.backend_id),
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
