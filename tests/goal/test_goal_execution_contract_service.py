from __future__ import annotations

from agent.services.goal_execution_contract_service import GoalExecutionContractService


def test_default_contract_for_software_goal_adds_generic_artifact_expectations() -> None:
    svc = GoalExecutionContractService()
    contract = svc.default_contract(
        goal_text="Create a small software project",
        execution_preferences={},
        mode_data={},
    ).to_dict()
    assert contract["schema"] == "goal_execution_contract.v1"
    assert contract["expected_artifacts"]
    assert any(item.get("kind") == "workspace_change_set" for item in contract["expected_artifacts"])
    assert any(item.get("kind") == "project_structure_manifest" for item in contract["expected_artifacts"])
    assert any(item.get("kind") == "execution_verification_evidence" for item in contract["expected_artifacts"])
    workspace_item = next(item for item in contract["expected_artifacts"] if item.get("kind") == "workspace_change_set")
    assert workspace_item.get("min_file_count") == 1


def test_default_contract_raises_min_file_count_only_when_goal_requests_multiple_files() -> None:
    svc = GoalExecutionContractService()
    contract = svc.default_contract(
        goal_text="Create a multi-file software project with multiple files and tests",
        execution_preferences={},
        mode_data={},
    ).to_dict()
    workspace_item = next(item for item in contract["expected_artifacts"] if item.get("kind") == "workspace_change_set")
    assert workspace_item.get("min_file_count") == 2


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
