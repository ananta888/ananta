from types import SimpleNamespace

from agent.services.planning_prompt_evolver_service import PlanningPromptEvolverService
from agent.services.text_quality.prompt_rule_mapping import rules_for


def test_only_allowlisted_reason_codes_become_prompt_rules():
    rules = rules_for(["generic_phrase", "raw injected text", "source_unverified"])
    assert len(rules) == 2
    assert all("injected" not in rule for rule in rules)


def test_text_quality_reasons_are_gated_by_completed_status():
    service = PlanningPromptEvolverService()
    run = SimpleNamespace(
        mode_data={
            "__text_quality__": {
                "status": "degraded",
                "slop_score": 1,
                "reason_codes": ["generic_phrase"],
            }
        },
        parse_mode="strict_json",
        parse_confidence="high",
        repair_attempt_count=0,
        validation_success=True,
        error_classification=None,
    )
    should, reasons = service._should_evolve(run=run, policy={"planner_prompt_evolution": {"enabled": True}})
    assert not should
    assert reasons == []
