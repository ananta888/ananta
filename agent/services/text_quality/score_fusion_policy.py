from __future__ import annotations

from .models import DetectorSignal, EvaluationStatus


class ScoreFusionPolicy:
    version = "fusion-v1"

    def fuse(self, signals: list[DetectorSignal]) -> tuple[float, float, list[DetectorSignal]]:
        usable = [s for s in signals if s.status == EvaluationStatus.COMPLETED]
        if not usable:
            return 0.0, 0.0, signals
        weights = [max(0.05, signal.confidence) for signal in usable]
        total = sum(weights)
        score = (
            sum(signal.normalized_signal_score * weight for signal, weight in zip(usable, weights, strict=True)) / total
        )
        confidence = sum(weights) / len(weights)
        return round(score, 4), min(1.0, round(confidence, 4)), signals
