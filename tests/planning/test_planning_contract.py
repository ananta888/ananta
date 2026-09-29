from agent.services.planning_contract import resolve_planning_contract
from agent.services.planning_validation_service import get_planning_validation_service


def test_default_software_contract_requires_core_kinds():
    contract = resolve_planning_contract(mode="new_software_project", planning_policy={})
    assert contract.required_task_kinds == ("analysis", "coding", "testing", "review")
    assert contract.min_tasks >= 4


def test_validation_returns_no_tasks_and_missing_kind_codes():
    contract = resolve_planning_contract(mode="new_software_project", planning_policy={})
    result = get_planning_validation_service().validate_subtasks(subtasks=[], contract=contract)
    assert result.ok is False
    assert "no_tasks" in result.error_codes
    assert "missing_required_task_kind" in result.error_codes


def test_validation_passes_for_complete_contract_plan():
    contract = resolve_planning_contract(mode="new_software_project", planning_policy={})
    subtasks = [
        {"title": "Analyse", "description": "Requirements and architecture", "task_kind": "analysis"},
        {"title": "Implement", "description": "Build service module", "task_kind": "coding"},
        {"title": "Test", "description": "Write unit and integration tests", "task_kind": "testing"},
        {"title": "Review", "description": "Review and docs", "task_kind": "review"},
    ]
    result = get_planning_validation_service().validate_subtasks(subtasks=subtasks, contract=contract)
    assert result.ok is True
    assert result.error_codes == ()


def test_validation_reports_too_few_and_missing_kinds_deterministically():
    contract = resolve_planning_contract(mode="new_software_project", planning_policy={})
    subtasks = [{"title": "Implement", "description": "Build endpoint", "task_kind": "coding"}]
    result = get_planning_validation_service().validate_subtasks(subtasks=subtasks, contract=contract)
    assert result.ok is False
    assert result.error_codes == ("too_few_tasks", "missing_required_task_kind")
    assert set(result.missing_task_kinds) == {"analysis", "testing", "review"}


def test_validation_reports_invalid_payload_code():
    contract = resolve_planning_contract(mode="generic", planning_policy={})
    subtasks = [{"task_kind": "coding"}]
    result = get_planning_validation_service().validate_subtasks(subtasks=subtasks, contract=contract)
    assert result.ok is False
    assert "invalid_task_payload" in result.error_codes
