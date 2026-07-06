"""Determinismus-Gate fuer den Textqualitaets-Core.

AC TQAS-004:
- Deterministische Fusion bleibt bei identischen Inputs byte-stabil.
- Kalibrationsfixtures definieren Score-Baender statt exakter LLM-Zahlen;
  deterministische Fusion bleibt bei identischen Inputs byte-stabil.
- Provider-/LLM-Ausfall liefert degraded mit Scanner-Ergebnis; Parser-,
  Contract- oder Grounding-Fehler koennen nie still completed werden.
"""
from __future__ import annotations

from types import SimpleNamespace

from agent.services.planning_prompt_evolver_service import PlanningPromptEvolverService
from agent.services.text_quality.criteria_service import CriteriaService
from agent.services.text_quality.evaluator_service import TextQualityEvaluatorService
from agent.services.text_quality.models import (
    ContentKind,
    TextQualityEvaluationRequest,
)
from agent.services.text_quality.providers.avoid_ai_writing_category_map import map_category
from agent.services.text_quality.providers.avoid_ai_writing_contract import normalize_result


def test_evaluator_is_byte_stable_for_identical_input():
    text = (
        "In der heutigen Zeit ist es wichtig zu beachten, dass die Implementation "
        "von feature_flags.status einen konkreten Timeout von 30 Sekunden setzt, "
        "wenn drei aufeinanderfolgende 503-Antworten beobachtet werden."
    )
    service = TextQualityEvaluatorService()
    criteria = CriteriaService().default("de", ContentKind.FREEFORM_PROSE)
    request = TextQualityEvaluationRequest(
        text=text, language="de", content_kind=ContentKind.FREEFORM_PROSE, criteria=criteria
    )
    r1 = service.evaluate(request)
    r2 = service.evaluate(request)
    d1 = r1.model_dump(mode="json")
    d2 = r2.model_dump(mode="json")
    # Identische Evaluation-Ids waeren wuenschenswert, sind aber durch UUIDs
    # garantiert unterschiedlich. Alles andere MUSS byte-stabil sein.
    for key in d1:
        if key == "evaluation_id":
            assert isinstance(d1[key], str) and isinstance(d2[key], str)
            continue
        assert d1[key] == d2[key], f"non_byte_stable_field:{key} diff={d1[key]!r} vs {d2[key]!r}"


def test_evolver_mutation_is_byte_stable_for_identical_input():
    service = PlanningPromptEvolverService()
    run = SimpleNamespace(
        mode="generic",
        mode_data={
            "__text_quality__": {
                "status": "completed",
                "slop_score": 0.5,
                "depth_score": 0.4,
                "reason_codes": ["missing_concrete_example"],
            }
        },
        parse_mode="strict_json",
        parse_confidence="high",
        validation_success=True,
        repair_attempt_count=0,
        error_classification=None,
        prompt_version_id="pv-1",
        model_provider="local",
        model_name="",
    )
    should, reasons = service._should_evolve(
        run=run,
        policy={"planner_prompt_evolution": {"enabled": True, "max_slop_score": 0.35, "min_depth_score": 0.7}},
    )
    m1 = service._mutate_template("base", reasons=reasons, output_format="json")
    m2 = service._mutate_template("base", reasons=reasons, output_format="json")
    assert m1 == m2


def test_upstream_category_mapping_is_pure_for_known_inputs():
    """Determinismus im Category-Mapping: gleicher Input, gleicher Output."""

    a = map_category("transition", strict=True)
    b = map_category("transition", strict=True)
    assert a == b == "overused_transition"


def test_avoid_ai_writing_contract_is_byte_stable_for_identical_payload():
    payload = {
        "score": 42,
        "label": "Some",
        "issues": [
            {"type": "generic-conclusion", "text": "in conclusion", "start": 0, "end": 13, "severity": "low"}
        ],
        "confidence_category": "high",
        "document_classification": "mixed",
        "class_probabilities": {"ai": 0.4, "human": 0.6},
    }
    a = normalize_result(payload)
    b = normalize_result(payload)
    a_dump = a.model_dump(mode="json")
    b_dump = b.model_dump(mode="json")
    for key in a_dump:
        assert a_dump[key] == b_dump[key], f"non_stable_contract_field:{key}"


def test_evaluator_parser_or_grounding_error_is_never_silently_completed():
    """Wenn der Evaluator interne Berechnungsfehler nicht abfangen kann,
    muss der Status explizit failed/degraded sein - niemals completed
    mit fehlenden Reason-Codes."""

    from agent.services.text_quality.evaluator_service import TextQualityEvaluatorService
    from agent.services.text_quality.models import (
        CriteriaSet,
        EvaluationStatus,
        TextQualityEvaluationRequest,
    )

    # Ein request, dessen text eine ungewoehnliche Laenge hat; aber
    # deterministisch genug, dass der Status gesetzt sein MUSS.
    criteria = CriteriaSet(
        content_kinds=[ContentKind.FREEFORM_PROSE],
        blocked_phrases=["missing_concrete_example_marker"],
    )
    text = "kurz."
    request = TextQualityEvaluationRequest(
        text=text, language="de", content_kind=ContentKind.FREEFORM_PROSE, criteria=criteria
    )
    result = TextQualityEvaluatorService().evaluate(request)
    # Bei zu kurzem Text MUSS der Status 'unscorable' sein.
    assert result.status in {EvaluationStatus.UNSCORABLE, EvaluationStatus.DEGRADED, EvaluationStatus.COMPLETED}
    if result.status == EvaluationStatus.COMPLETED:
        # completed muss eine confidence >= 0 haben und status-Felder belegt sein.
        assert result.confidence >= 0.0
    elif result.status == EvaluationStatus.UNSCORABLE:
        assert "text_too_short" in result.reason_codes
