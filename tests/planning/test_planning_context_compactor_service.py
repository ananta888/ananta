from agent.services.planning_context_compactor_service import (
    PlanningContextCompactorService,
    ERR_CONTRACT_CONSTRAINT_LOSS,
)
from agent.services.propose_policy import ProposePolicy


def test_compactor_deterministic_fallback_schema():
    svc = PlanningContextCompactorService()
    policy = ProposePolicy(context_compaction_enabled=False)
    out = svc.compact(
        goal_text="Build secure reviewable pipeline",
        context_text="Include security policy and verification steps",
        mode="coding",
        mode_data={"history": ["x" * 5000]},
        planning_policy={},
        llm_config={},
        policy=policy,
    )
    payload = out.payload
    assert "goal_summary" in payload
    assert isinstance(payload.get("hard_constraints"), list)
    assert isinstance(payload.get("non_negotiables"), list)
    assert isinstance((payload.get("compactor_meta") or {}).get("truncated_fields"), list)


def test_compactor_detects_constraint_loss():
    svc = PlanningContextCompactorService()
    ok, err = svc._validate_payload(
        {
            "goal_summary": "x",
            "hard_constraints": [],
            "non_negotiables": [],
            "relevant_context": [],
            "omitted_context_summary": "x",
            "risks": [],
            "open_questions": [],
        },
        max_output_chars=2000,
        hard_constraints=["preserve:security"],
        non_negotiables=["must_keep_security"],
    )
    assert ok is False
    assert err == ERR_CONTRACT_CONSTRAINT_LOSS


def test_compactor_pretrim_marks_truncated_fields():
    svc = PlanningContextCompactorService()
    tr = []
    out = svc._pre_trim({"history": "a" * 2000, "nested": {"logs": "b" * 2000}}, max_chars=200, max_items=10, truncated_fields=tr)
    assert isinstance(out, dict)
    assert tr
