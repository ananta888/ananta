from agent.services.task_state_machine_service import (
    build_task_state_machine_contract,
    build_task_status_contract,
    can_autopilot_dispatch,
    can_transition,
    resolve_next_status,
)


def test_task_status_contract_exposes_canonical_and_alias_values():
    contract = build_task_status_contract()

    assert "todo" in contract.canonical_values
    assert "completed" in contract.terminal_values
    assert "assigned" in contract.autopilot_dispatch_values
    assert contract.aliases["done"] == "completed"


def test_task_state_machine_contract_exposes_transition_rules():
    contract = build_task_state_machine_contract()

    retry_rule = next(rule for rule in contract.transitions if rule.action == "retry")
    assert "failed" in retry_rule.from_statuses
    assert retry_rule.to_status == "todo"


def test_state_machine_helpers_honor_assignment_and_manual_override():
    ok, reason = can_transition("pause", "assigned")
    assert ok is True
    assert reason == ""
    assert resolve_next_status("retry", "failed", assigned_agent_url="http://worker") == "assigned"
    assert can_autopilot_dispatch("todo", manual_override_active=True) is False
