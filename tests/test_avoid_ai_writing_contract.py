import pytest

from agent.services.text_quality.providers.avoid_ai_writing_contract import (
    normalize_result,
)
from agent.services.text_quality.providers.avoid_ai_writing_category_map import (
    KNOWN_UPSTREAM_TYPES,
)


def test_upstream_score_remains_detector_signal():
    result = normalize_result(
        {
            "score": 75,
            "label": "Strong",
            "issues": [
                {
                    "type": "generic-conclusion",
                    "text": "the future looks bright",
                    "severity": "high",
                }
            ],
            "confidence_category": "high",
        }
    )
    assert result.raw_signal_score == 75
    assert result.normalized_signal_score == 0.75
    assert result.reason_codes == ["generic_phrase"]


def test_unscorable_and_upstream_drift():
    assert len(KNOWN_UPSTREAM_TYPES) == 44
    assert normalize_result({"label": "Too short"}).status.value == "unscorable"
    with pytest.raises(ValueError, match="upstream_unknown"):
        normalize_result(
            {
                "score": 1,
                "label": "Minimal",
                "issues": [{"type": "new-upstream-type"}],
            }
        )
