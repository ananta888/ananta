from agent.services.worker_capability_service import get_worker_capability_service


def test_worker_capability_profiles_are_formal_and_hub_bounded():
    profiles = get_worker_capability_service().build_worker_capability_profiles()

    assert {"planning-agent", "planner", "coder", "reviewer", "operator"}.issubset(set(profiles))
    planning_agent = profiles["planning-agent"]
    assert "plan_proposal" in planning_agent["allowed_scopes"]
    assert "no_plan_acceptance" in planning_agent["limits"]
    coder = profiles["coder"]
    assert coder["contract_version"] == "v1"
    assert coder["orchestration_boundary"] == "hub_owned_task_queue"
    assert coder["worker_rule"] == "execute_delegated_work_only"
    assert "code_change" in coder["allowed_scopes"]
    assert "hub_assigned_tasks_only" in coder["limits"]
