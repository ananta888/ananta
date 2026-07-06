from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import DetectorSignal, TextQualityEvaluationRequest


@runtime_checkable
class QualitySignalProvider(Protocol):
    def analyze(self, request: TextQualityEvaluationRequest) -> DetectorSignal: ...
