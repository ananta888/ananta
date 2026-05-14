from __future__ import annotations

from agent.services.goal_execution_contract_service import GoalExecutionContractService


def test_default_contract_for_software_goal_adds_artifact_expectations() -> None:
    svc = GoalExecutionContractService()
    contract = svc.default_contract(
        goal_text="Create a small Java backend and Angular frontend for Fibonacci",
        execution_preferences={},
        mode_data={},
    ).to_dict()
    assert contract["schema"] == "goal_execution_contract.v1"
    assert contract["expected_artifacts"]
    assert any(item.get("relative_path") == "backend" for item in contract["expected_artifacts"])
    assert any(item.get("relative_path") == "frontend" for item in contract["expected_artifacts"])


def test_attach_to_execution_preferences_is_backward_compatible() -> None:
    svc = GoalExecutionContractService()
    prefs = svc.attach_to_execution_preferences(
        goal_text="Simple analysis goal",
        execution_preferences={"output_dir": "/tmp/x"},
        mode_data={},
    )
    assert prefs["output_dir"] == "/tmp/x"
    assert isinstance(prefs.get("goal_execution_contract"), dict)
    assert prefs["goal_execution_contract"]["version"] == "v1"


def test_task_scoped_contract_propagates_expected_artifacts() -> None:
    svc = GoalExecutionContractService()
    scoped = svc.task_scoped_contract(
        goal_contract={"version": "v1", "execution_mode": "llm_first_with_guardrails"},
        plan_id="plan-1",
        plan_node_id="N1",
        expected_artifacts=[{"kind": "directory", "required": True, "relative_path": "backend"}],
    )
    assert scoped["schema"] == "worker_execution_contract.v1"
    assert scoped["traceability"]["plan_node_id"] == "N1"
    assert scoped["expected_artifacts"][0]["relative_path"] == "backend"

