from agent.services.llm_first_planning_orchestrator_service import get_llm_first_planning_orchestrator_service


def test_new_project_prefers_llm_first_even_with_template():
    svc = get_llm_first_planning_orchestrator_service()
    dec = svc.decide_strategy_order(mode="new_software_project", use_template=True, use_repo_context=True, planning_policy={"llm_first": False})
    assert dec.strategy_order[0] == "llm"


def test_template_first_when_policy_disables_llm_first():
    svc = get_llm_first_planning_orchestrator_service()
    dec = svc.decide_strategy_order(mode="generic", use_template=True, use_repo_context=True, planning_policy={"llm_first": False})
    assert dec.strategy_order[0] == "template"
