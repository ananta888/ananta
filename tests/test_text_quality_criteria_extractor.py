from types import SimpleNamespace

import pytest

from agent.services.text_quality import criteria_extractor_service as module
from agent.services.text_quality.criteria_extractor_service import CriteriaExtractorService
from agent.services.text_quality.models import ContentKind


def test_extractor_redacts_examples_and_creates_reviewable_proposal(monkeypatch):
    captured = {}

    def invoke(**kwargs):
        captured.update(kwargs)
        return {
            "blocked_phrases": ["Es ist wichtig zu beachten"],
            "required_positive_traits": ["Konkrete Begründung"],
            "reason_codes": ["generic_phrase"],
            "confidence": 0.6,
        }

    monkeypatch.setattr(
        module,
        "get_criteria_service",
        lambda: SimpleNamespace(create=lambda criteria: criteria),
    )
    result = CriteriaExtractorService(invoke).extract(
        examples=["Kontakt max@example.com token=secret"],
        language="de",
        content_kind=ContentKind.FREEFORM_PROSE,
        actor="reviewer",
        comments="password=hunter2",
    )
    assert "max@example.com" not in captured["prompt"]
    assert "hunter2" not in captured["prompt"]
    assert result.status == "proposed"
    assert result.requires_review is True
    assert result.source_refs[0]["sha256"]


def test_extractor_rejects_unknown_reason_codes(monkeypatch):
    monkeypatch.setattr(
        module,
        "get_criteria_service",
        lambda: SimpleNamespace(create=lambda criteria: criteria),
    )
    service = CriteriaExtractorService(
        lambda **_: {
            "blocked_phrases": [],
            "required_positive_traits": [],
            "reason_codes": ["change_governance"],
            "confidence": 1,
        }
    )
    with pytest.raises(ValueError, match="unknown_reason_code"):
        service.extract(
            examples=["Ein langes negatives Beispiel mit genügend Inhalt."],
            language="de",
            content_kind=ContentKind.FREEFORM_PROSE,
            actor="reviewer",
        )

