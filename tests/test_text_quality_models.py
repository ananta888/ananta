import math

import pytest
from pydantic import ValidationError

from agent.services.text_quality.models import (
    ContentKind,
    CriteriaSet,
    DetectorSignal,
    TextQualityEvaluationResult,
)


def test_criteria_checksum_is_stable_and_ignores_runtime_identity():
    left = CriteriaSet(
        content_kinds=[ContentKind.FREEFORM_PROSE],
        blocked_phrases=["moreover"],
    )
    right = CriteriaSet(
        content_kinds=[ContentKind.FREEFORM_PROSE],
        blocked_phrases=["moreover"],
    )
    assert left.id != right.id
    assert left.canonical_checksum() == right.canonical_checksum()


def test_scores_are_bounded_and_unknown_reason_codes_fail():
    signal = DetectorSignal(
        provider_name="test",
        provider_version="1",
        normalized_signal_score=2,
        confidence=-1,
    )
    assert signal.normalized_signal_score == 1
    assert signal.confidence == 0
    with pytest.raises(ValidationError):
        DetectorSignal(
            provider_name="test",
            provider_version="1",
            normalized_signal_score=math.nan,
        )
    with pytest.raises(ValidationError):
        TextQualityEvaluationResult(
            slop_score=0,
            depth_score=0,
            style_fit_score=0,
            confidence=0,
            criteria_version="1",
            language="de",
            content_kind=ContentKind.FREEFORM_PROSE,
            status="completed",
            reason_codes=["invented"],
        )
