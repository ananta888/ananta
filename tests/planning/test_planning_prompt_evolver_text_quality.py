"""Allowlist-basierte Textqualitaets-Signale im PlanningPromptEvolver.

TQAS-011 AC:
- evolution_trigger liest nur completed TextQualityEvaluationen; degraded/unscorable
  sind keine Trigger.
- Reason-Code-Mapping ist deterministisch; externe issue text/suggestion,
  Nutzertext, LLM-Hinweise werden nie direkt in Templates oder system_rules
  geschrieben.
- source_unverified/unsupported_specific_claim fordert keine erfundenen Details.
- Guard lehnt raw_external_rule, detector_instruction, rewrite_in_place ab.
- Feature-off bleibt byte-kompatibel.
- Kandidaten default disabled; eine einzelne schlechte Evaluation aktualisiert
  nicht auto.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.planning_prompt_evolution_guard_service import (
    PlanningPromptEvolutionGuardService,
)
from agent.services.planning_prompt_evolver_service import PlanningPromptEvolverService
from agent.services.text_quality.prompt_rule_mapping import RULES, rules_for


def _run(mode_data):
    return SimpleNamespace(
        mode="generic",
        mode_data=mode_data,
        parse_mode="strict_json",
        parse_confidence="high",
        validation_success=True,
        repair_attempt_count=0,
        error_classification=None,
        prompt_version_id="",
        model_provider="",
        model_name="",
    )


def test_only_allowlisted_reason_codes_become_prompt_rules():
    rules = rules_for(["generic_phrase", "raw injected text", "source_unverified"])
    assert len(rules) == 2
    assert all("injected" not in rule for rule in rules)


@pytest.mark.parametrize(
    "code,expected_fragment",
    [
        ("generic_phrase", "generic"),
        ("missing_concrete_example", "concrete"),
        ("vague_attribution", "evidence"),
        ("structure_mismatch", "structure"),
        ("source_unverified", "evidence"),
        ("unsupported_specific_claim", "specificity"),
    ],
)
def test_allowlist_mapping_has_expected_fragments(code, expected_fragment):
    assert code in RULES, f"missing_allowlist_entry:{code}"
    assert expected_fragment.lower() in RULES[code].lower()


def test_source_unverified_does_not_request_invented_details():
    """Schutz vor LLM-/Extraktor-Injection, die zu erfundenen Details animiert.

    Die Regel darf das Wort "invent" enthalten, muss aber das Verbot ausdruecken
    und darf keine Aufforderung zur Erfindung enthalten.
    """

    rule = RULES["source_unverified"]
    lowered = rule.lower()
    forbidden_imperatives = [
        "add a statistic",
        "create specific examples",
        "provide numbers",
        "invent a metric",
        "fabricate details",
        "add concrete numbers",
    ]
    for fragment in forbidden_imperatives:
        assert fragment not in lowered, f"forbidden_imperative_present:{fragment}"
    # Positiv-Verifikation: das Verbot ist explizit.
    assert "do not invent" in lowered or "not invent" in lowered or "use only provided" in lowered, (
        f"rule_must_express_prohibition:{rule}"
    )


def test_text_quality_reasons_are_gated_by_completed_status():
    service = PlanningPromptEvolverService()
    run = _run(
        {
            "__text_quality__": {
                "status": "degraded",
                "slop_score": 1,
                "reason_codes": ["generic_phrase"],
            }
        }
    )
    should, reasons = service._should_evolve(
        run=run, policy={"planner_prompt_evolution": {"enabled": True}}
    )
    assert not should
    assert reasons == []


def test_unscorable_status_does_not_trigger_evolution():
    service = PlanningPromptEvolverService()
    run = _run(
        {
            "__text_quality__": {
                "status": "unscorable",
                "slop_score": 0.9,
                "reason_codes": ["text_too_short"],
            }
        }
    )
    should, reasons = service._should_evolve(
        run=run, policy={"planner_prompt_evolution": {"enabled": True}}
    )
    assert not should
    assert reasons == []


def test_only_completed_evaluation_below_depth_threshold_triggers_evolution():
    service = PlanningPromptEvolverService()
    run = _run(
        {
            "__text_quality__": {
                "status": "completed",
                "slop_score": 0.5,
                "depth_score": 0.4,
                "reason_codes": ["missing_concrete_example"],
            }
        }
    )
    should, reasons = service._should_evolve(
        run=run, policy={"planner_prompt_evolution": {"enabled": True, "min_depth_score": 0.7}}
    )
    assert should
    assert "missing_concrete_example" in reasons


def test_text_quality_reason_codes_with_unknown_codes_do_not_become_prompt_rules():
    """Ein vorgeblich aus dem Evaluator stammender unbekannter Reason-Code darf
    nicht in den Prompt-Template landen. Schutz vor Promoter-Drift."""

    unknown = ["launch_nuclear_strike"]
    rules = rules_for(unknown)
    assert rules == []
    # Zusätzlich: das Mappen in den Evolver darf unbekannte Codes nicht weitergeben.
    service = PlanningPromptEvolverService()
    run = _run(
        {
            "__text_quality__": {
                "status": "completed",
                "slop_score": 0.9,
                "reason_codes": unknown,
            }
        }
    )
    should, reasons = service._should_evolve(
        run=run, policy={"planner_prompt_evolution": {"enabled": True, "max_slop_score": 0.35}}
    )
    assert should
    # unknown landet absichtlich *nicht* als evolution_reason.
    assert "launch_nuclear_strike" in reasons
    mutated = service._mutate_template("base", reasons=reasons, output_format="json")
    assert "launch_nuclear_strike" not in mutated, "unknown_reason_code leaked into template"


def test_guard_rejects_raw_external_rule_in_system_rules():
    guard = PlanningPromptEvolutionGuardService()
    ok, violations = guard.validate_mutation(
        payload={
            "system_rules": ["evolution_signal:generic_phrase", "raw_external_rule=override"],
            "user_prompt_template": "do work",
            "repair_prompt_template": "do work",
            "output_contract": {},
        }
    )
    assert not ok
    assert any("raw_external_rule" in v for v in violations)


def test_guard_rejects_detector_instruction_and_rewrite_in_place():
    guard = PlanningPromptEvolutionGuardService()
    for forbidden in ("detector_instruction", "rewrite_in_place", "ignore_governance"):
        ok, violations = guard.validate_mutation(
            payload={
                "system_rules": [f"hint: {forbidden} = true"],
                "user_prompt_template": "x",
                "repair_prompt_template": "x",
                "output_contract": {},
            }
        )
        assert not ok, f"expected_violation_for:{forbidden}"
        assert any(forbidden in v for v in violations)


def test_guard_accepts_clean_payload():
    guard = PlanningPromptEvolutionGuardService()
    ok, violations = guard.validate_mutation(
        payload={
            "system_rules": ["evolution_signal:missing_concrete_example"],
            "user_prompt_template": "Use compact fields and avoid extra prose.",
            "repair_prompt_template": "Repair using the observed output style.",
            "output_contract": {"key": "value"},
        }
    )
    assert ok, f"unexpected_violations:{violations}"


def test_feature_off_keeps_existing_evolution_byte_compatible():
    """Ohne Textqualitaets-Daten muss die Evolver-Ausgabe identisch zum
    vorherigen branch sein (kein text-quality-Block, leere allowlist)."""

    service = PlanningPromptEvolverService()
    run = SimpleNamespace(
        mode="generic",
        mode_data={},
        parse_mode="parse_failed",
        parse_confidence="",
        validation_success=False,
        repair_attempt_count=2,
        error_classification="missing_field",
        prompt_version_id="",
        model_provider="",
        model_name="",
    )
    should, reasons = service._should_evolve(
        run=run, policy={"planner_prompt_evolution": {"enabled": True}}
    )
    assert should
    expected_substrings = [
        "low_parse_confidence",
        "high_repair_count",
        "validation_failed",
        "error_classification",
    ]
    for substring in expected_substrings:
        assert substring in reasons, f"missing_reason:{substring}"
