"""Provider-neutral text-quality evaluation subsystem."""

from .evaluator_service import TextQualityEvaluatorService
from .models import (
    ContentKind,
    CriteriaSet,
    DetectorSignal,
    EvaluationStatus,
    TextQualityEvaluationRequest,
    TextQualityEvaluationResult,
)

__all__ = [
    "ContentKind",
    "CriteriaSet",
    "DetectorSignal",
    "EvaluationStatus",
    "TextQualityEvaluationRequest",
    "TextQualityEvaluationResult",
    "TextQualityEvaluatorService",
]
