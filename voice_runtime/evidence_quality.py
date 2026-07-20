"""Deterministic bounded quality evaluation for speech evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


class SpeechEvidenceQualityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechEvidenceQualityDecision:
    accepted: bool
    reason_codes: tuple[str, ...]
    normalized_metrics: dict[str, float | int]


class SpeechEvidenceQualityPolicy:
    VERSION = "speech-evidence-quality-v1"

    def __init__(
        self,
        *,
        min_duration_ms: int = 250,
        max_duration_ms: int = 120_000,
        min_snr_db: float = 8.0,
        max_clipping_ratio: float = 0.02,
        max_silence_ratio: float = 0.92,
    ) -> None:
        self._min_duration = min_duration_ms
        self._max_duration = max_duration_ms
        self._min_snr = min_snr_db
        self._max_clipping = max_clipping_ratio
        self._max_silence = max_silence_ratio

    def evaluate(self, metrics: Mapping[str, object]) -> SpeechEvidenceQualityDecision:
        if set(metrics) != {"duration_ms", "snr_db", "clipping_ratio", "silence_ratio"}:
            raise SpeechEvidenceQualityError("speech_quality_metrics_schema_invalid")
        duration = _integer(metrics["duration_ms"], "duration_ms")
        snr = _number(metrics["snr_db"], "snr_db")
        clipping = _ratio(metrics["clipping_ratio"], "clipping_ratio")
        silence = _ratio(metrics["silence_ratio"], "silence_ratio")
        reasons: list[str] = []
        if duration < self._min_duration:
            reasons.append("speech_quality_duration_too_short")
        if duration > self._max_duration:
            reasons.append("speech_quality_duration_too_long")
        if snr < self._min_snr:
            reasons.append("speech_quality_snr_too_low")
        if clipping > self._max_clipping:
            reasons.append("speech_quality_clipping_excessive")
        if silence > self._max_silence:
            reasons.append("speech_quality_silence_excessive")
        return SpeechEvidenceQualityDecision(
            accepted=not reasons,
            reason_codes=tuple(reasons),
            normalized_metrics={
                "duration_ms": duration,
                "snr_db": round(snr, 3),
                "clipping_ratio": round(clipping, 6),
                "silence_ratio": round(silence, 6),
            },
        )


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3_600_000:
        raise SpeechEvidenceQualityError(f"speech_quality_{field}_invalid")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechEvidenceQualityError(f"speech_quality_{field}_invalid")
    number = float(value)
    if not math.isfinite(number) or not -100 <= number <= 200:
        raise SpeechEvidenceQualityError(f"speech_quality_{field}_invalid")
    return number


def _ratio(value: object, field: str) -> float:
    number = _number(value, field)
    if not 0 <= number <= 1:
        raise SpeechEvidenceQualityError(f"speech_quality_{field}_invalid")
    return number


__all__ = [
    "SpeechEvidenceQualityDecision",
    "SpeechEvidenceQualityError",
    "SpeechEvidenceQualityPolicy",
]
