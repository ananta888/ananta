"""Deterministic semantic-speech quality and fallback policy."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ananta_contracts.semantic_speech import SpeechMode

QUALITY_POLICY_VERSION = 1
MIN_MODE_HOLD_MS = 5_000


@dataclass(frozen=True, slots=True)
class SpeechQualityThresholds:
    loss_ratio: float = 0.08
    queue_bytes: int = 3 * 1024 * 1024
    partial_age_ms: int = 1_000
    correction_lag_ms: int = 120_000
    source_loss_ratio: float = 0.20
    feature_loss_ratio: float = 0.25
    reconstruction_error_ratio: float = 0.20


@dataclass(frozen=True, slots=True)
class SpeechQualitySample:
    measured_at_ms: int
    loss_ratio: float
    queue_bytes: int
    partial_age_ms: int
    correction_lag_ms: int
    source_loss_ratio: float
    feature_loss_ratio: float
    reconstruction_error_ratio: float


@dataclass(frozen=True, slots=True)
class SpeechQualityDecision:
    mode: SpeechMode
    reason_code: str
    transitioned: bool
    ordinary_audio_available: bool
    live_transcript_enabled: bool
    delayed_source_enabled: bool
    semantic_features_enabled: bool
    policy_version: int = QUALITY_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class SpeechTransportFailureAction:
    status: int
    purge_session: bool
    drop_current_segment: bool
    retry_allowed: bool
    reduce_segment_size: bool
    reason_code: str


class SpeechQualityPolicy:
    """Pure policy with a bounded single transition timestamp and no timers."""

    def __init__(
        self,
        *,
        initial_mode: SpeechMode = "ordinary_audio",
        thresholds: SpeechQualityThresholds | None = None,
    ) -> None:
        self._thresholds = thresholds or SpeechQualityThresholds()
        self._mode = initial_mode
        self._last_transition_ms: int | None = None

    def evaluate(
        self,
        sample: SpeechQualitySample,
        *,
        desired_mode: SpeechMode,
        revoked: bool = False,
        user_ordinary_override: bool = False,
        semantic_runtime_failed: bool = False,
    ) -> SpeechQualityDecision:
        self._validate(sample)
        if revoked:
            return self._transition("ordinary_audio", "consent_revoked", sample.measured_at_ms, immediate=True)
        if user_ordinary_override:
            return self._transition("ordinary_audio", "user_ordinary_override", sample.measured_at_ms, immediate=True)

        target, reason = self._target(sample, desired_mode, semantic_runtime_failed)
        return self._transition(target, reason, sample.measured_at_ms, immediate=False)

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": self._mode,
            "last_transition_ms": self._last_transition_ms,
            "timers": 0,
        }

    def _target(
        self,
        sample: SpeechQualitySample,
        desired_mode: SpeechMode,
        semantic_runtime_failed: bool,
    ) -> tuple[SpeechMode, str]:
        threshold = self._thresholds
        if semantic_runtime_failed:
            return "ordinary_audio", "semantic_runtime_failed"
        if sample.loss_ratio > threshold.loss_ratio:
            return "ordinary_audio", "packet_loss_high"
        if sample.queue_bytes > threshold.queue_bytes:
            return "ordinary_audio", "speech_queue_high"
        if sample.partial_age_ms > threshold.partial_age_ms:
            return "ordinary_audio", "live_partial_stale"
        if sample.reconstruction_error_ratio > threshold.reconstruction_error_ratio:
            return "transcript_live", "reconstruction_quality_low"
        if sample.source_loss_ratio > threshold.source_loss_ratio:
            return "transcript_live", "source_loss_high"
        if sample.correction_lag_ms > threshold.correction_lag_ms:
            return "transcript_live", "correction_lag_high"
        if sample.feature_loss_ratio > threshold.feature_loss_ratio:
            return "transcript_live", "feature_loss_high"
        if desired_mode not in {
            "ordinary_audio",
            "transcript_live",
            "semantic_reconstruction",
            "delayed_correction",
            "segment_only",
            "fallback",
        }:
            return "ordinary_audio", "desired_mode_invalid"
        return desired_mode, "quality_healthy"

    def _transition(
        self,
        target: SpeechMode,
        reason_code: str,
        now_ms: int,
        *,
        immediate: bool,
    ) -> SpeechQualityDecision:
        transitioned = False
        effective_reason = reason_code
        if target != self._mode:
            allowed = (
                immediate or self._last_transition_ms is None or now_ms - self._last_transition_ms >= MIN_MODE_HOLD_MS
            )
            if allowed:
                self._mode = target
                self._last_transition_ms = now_ms
                transitioned = True
            else:
                effective_reason = "quality_hysteresis_hold"
        mode = self._mode
        return SpeechQualityDecision(
            mode=mode,
            reason_code=effective_reason,
            transitioned=transitioned,
            ordinary_audio_available=True,
            live_transcript_enabled=mode not in {"segment_only"},
            delayed_source_enabled=mode in {"semantic_reconstruction", "delayed_correction"},
            semantic_features_enabled=mode == "semantic_reconstruction",
        )

    @staticmethod
    def _validate(sample: SpeechQualitySample) -> None:
        ratios = (
            sample.loss_ratio,
            sample.source_loss_ratio,
            sample.feature_loss_ratio,
            sample.reconstruction_error_ratio,
        )
        if any(not math.isfinite(item) or not 0 <= item <= 1 for item in ratios):
            raise ValueError("speech_quality_ratio_invalid")
        integers = (
            sample.measured_at_ms,
            sample.queue_bytes,
            sample.partial_age_ms,
            sample.correction_lag_ms,
        )
        if any(type(item) is not int or item < 0 for item in integers):
            raise ValueError("speech_quality_measurement_invalid")


def classify_speech_transport_failure(status: int) -> SpeechTransportFailureAction:
    if status in {404, 409}:
        return SpeechTransportFailureAction(status, True, False, False, False, "speech_session_gone")
    if status == 413:
        return SpeechTransportFailureAction(status, False, True, True, True, "speech_segment_too_large")
    if status in {408, 425, 429, 500, 502, 503, 504}:
        return SpeechTransportFailureAction(status, False, False, True, False, "speech_transport_retryable")
    return SpeechTransportFailureAction(status, False, False, False, False, "speech_transport_failed")


__all__ = [
    "MIN_MODE_HOLD_MS",
    "QUALITY_POLICY_VERSION",
    "SpeechQualityDecision",
    "SpeechQualityPolicy",
    "SpeechQualitySample",
    "SpeechQualityThresholds",
    "SpeechTransportFailureAction",
    "classify_speech_transport_failure",
]
