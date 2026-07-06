from agent.services.text_quality.criteria_service import CriteriaService
from agent.services.text_quality.evaluator_service import TextQualityEvaluatorService
from agent.services.text_quality.models import (
    ContentKind,
    TextQualityEvaluationRequest,
)


def test_german_slop_and_grounding_are_detected():
    criteria = CriteriaService().default("de", ContentKind.FREEFORM_PROSE)
    request = TextQualityEvaluationRequest(
        text=(
            "In der heutigen Zeit ist es wichtig zu beachten, dass SRC_UNKNOWN "
            "42 konkrete Vorteile bietet und darueber hinaus alles verbessert."
        ),
        criteria=criteria,
        evidence_refs=[{"id": "SRC_ALLOWED"}],
    )
    result = TextQualityEvaluatorService().evaluate(request)
    assert result.slop_score > 0.35
    assert result.grounding_status == "unverified"
    assert "source_unverified" in result.reason_codes


def test_structured_plan_does_not_receive_listicle_signal():
    criteria = CriteriaService().default("de", ContentKind.STRUCTURED_PLAN)
    result = TextQualityEvaluatorService().evaluate(
        TextQualityEvaluationRequest(
            text="1. API implementieren\n2. Tests ausfuehren\n3. Deployment pruefen",
            criteria=criteria,
            content_kind=ContentKind.STRUCTURED_PLAN,
        )
    )
    assert "structure_mismatch" not in result.reason_codes
