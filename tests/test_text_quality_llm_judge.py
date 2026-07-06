from agent.services.text_quality.criteria_service import CriteriaService
from agent.services.text_quality.llm_judge import LLMTextQualityJudge
from agent.services.text_quality.models import ContentKind, TextQualityEvaluationRequest


def _request():
    return TextQualityEvaluationRequest(
        text="Der Dienst setzt nach drei Fehlern für 30 Sekunden aus.",
        language="de",
        content_kind=ContentKind.TECHNICAL_DOCUMENTATION,
        criteria=CriteriaService().default("de", ContentKind.TECHNICAL_DOCUMENTATION),
    )


def test_llm_judge_accepts_only_closed_contract():
    judge = LLMTextQualityJudge(
        lambda **_: {
            "slop_signal": 0.1,
            "confidence": 0.8,
            "reason_codes": [],
            "improvement_hints": ["Fehlercode ergänzen."],
        }
    )
    signal = judge.analyze(_request())
    assert signal.normalized_signal_score == 0.1
    assert signal.metadata["improvement_hints"] == ["Fehlercode ergänzen."]


def test_llm_judge_fails_degraded_on_unknown_reason():
    judge = LLMTextQualityJudge(
        lambda **_: {
            "slop_signal": 0.1,
            "confidence": 0.8,
            "reason_codes": ["execute_tool"],
            "improvement_hints": [],
        }
    )
    assert judge.analyze(_request()).status.value == "degraded"

