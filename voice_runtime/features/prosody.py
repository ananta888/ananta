"""Bounded normalized prosody features; never emits PCM samples."""

from __future__ import annotations

import array
import math
from collections.abc import Callable

from .base import AcousticFeatureFrame


class BoundedProsodyExtractor:
    algorithm_version = "ananta.prosody.summary.v1"
    max_samples = 160_000

    def extract(
        self,
        *,
        pcm_s16le: bytes,
        sample_rate_hz: int,
        cancelled: Callable[[], bool] | None = None,
    ) -> AcousticFeatureFrame:
        if sample_rate_hz not in {8_000, 16_000, 24_000, 44_100, 48_000}:
            raise ValueError("prosody_sample_rate_unsupported")
        if len(pcm_s16le) % 2 or len(pcm_s16le) > self.max_samples * 2:
            raise ValueError("prosody_pcm_budget_invalid")
        if cancelled and cancelled():
            raise RuntimeError("prosody_cancelled")
        samples = array.array("h")
        samples.frombytes(pcm_s16le)
        if not samples:
            return AcousticFeatureFrame(self.algorithm_version, 0, 0.0, (0.0,), (0.0, 0.0), (0.0,))
        stride = max(1, len(samples) // 16_000)
        bounded = samples[::stride]
        scale = 32768.0
        peak = max(abs(sample) for sample in bounded) / scale
        mean_square = sum((sample / scale) ** 2 for sample in bounded) / len(bounded)
        rms = min(1.0, math.sqrt(mean_square))
        crossings = sum(
            1 for left, right in zip(bounded, bounded[1:], strict=False) if (left < 0 <= right) or (right < 0 <= left)
        )
        crossing_ratio = crossings / max(1, len(bounded) - 1)
        clipping = sum(1 for sample in bounded if abs(sample) >= 32_440) / len(bounded)
        pitch = self._pitch_summary(bounded, sample_rate_hz // stride, rms)
        duration_ms = min(1.0, len(samples) / sample_rate_hz / 10.0)
        confidence = 0.0 if rms < 0.002 else max(0.0, min(1.0, 1.0 - clipping * 4.0))
        values = (pitch, rms, peak, crossing_ratio, clipping, duration_ms, confidence)
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
            raise ValueError("prosody_non_finite")
        return AcousticFeatureFrame(
            algorithm_version=self.algorithm_version,
            window_ms=round(len(samples) / sample_rate_hz * 1000),
            confidence=confidence,
            pitch=(pitch,),
            energy=(rms, peak, clipping),
            timing=(crossing_ratio, duration_ms),
        )

    @staticmethod
    def _pitch_summary(samples: array.array, sample_rate_hz: int, rms: float) -> float:
        if rms < 0.002 or len(samples) < 64:
            return 0.0
        minimum_lag = max(1, sample_rate_hz // 400)
        maximum_lag = min(len(samples) // 2, sample_rate_hz // 70)
        if maximum_lag <= minimum_lag:
            return 0.0
        step = max(1, (maximum_lag - minimum_lag) // 64)
        best_lag = minimum_lag
        best_score = float("-inf")
        for lag in range(minimum_lag, maximum_lag + 1, step):
            score = sum(int(samples[index]) * int(samples[index - lag]) for index in range(lag, len(samples)))
            if score > best_score:
                best_score, best_lag = score, lag
        frequency = sample_rate_hz / best_lag
        return max(0.0, min(1.0, (frequency - 70.0) / 330.0))


__all__ = ["BoundedProsodyExtractor"]
