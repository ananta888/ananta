from agent.services.goal_planning_intent_service import get_goal_planning_intent_service


def test_classifies_software_goal():
    svc = get_goal_planning_intent_service()
    res = svc.classify("Build backend API service", mode="generic")
    assert res["goal_type"] == "software_project"


def test_classifies_research_goal():
    svc = get_goal_planning_intent_service()
    res = svc.classify("Research and compare options", mode="generic")
    assert res["goal_type"] == "research"
