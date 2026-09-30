"""Low-confidence PCM-region rerun stage of the transcription pipeline.

Only calibrated low-confidence segments are re-executed, each bounded by the
shared candidate deadline and the effective audio budget. There is no
full-audio fallback.
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
from .routing import RerunRegion, merge_regional_segments


class ConfidenceRerunStage:
    """Rerun low-confidence regions on the configured rerun backend."""

    def __init__(
        self,
        *,
        config: VoiceRuntimeConfig,
        backend_selector: PipelineBackendSelector,
        candidate_runner: BoundedCandidateRunner,
        candidate_scorer: CandidateScorer,
    ) -> None:
        self._config = config
        self._backend_selector = backend_selector
        self._candidate_runner = candidate_runner
        self._candidate_scorer = candidate_scorer

    def apply(
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
        calibration = calibration_for_result(self._candidate_scorer, result)
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
        rerun_backend = self._backend_selector.backend_for_id(self._config.rerun_backend)
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
            candidate, raw_rerun = self._candidate_runner.execute(
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
            rerun = raw_rerun or result_from_candidate(candidate)
            replacements[region_id] = (
                regional_replacement(
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
