from agent.services.task_state_machine_service import (
    build_task_state_machine_contract,
    build_task_status_contract,
    can_autopilot_dispatch,
    can_transition,
    can_transition_to,
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


def test_assignment_promoted_targets_are_persistable():
    for action, current_status in (
        ("resume", "paused"),
        ("retry", "failed"),
        ("retry", "cancelled"),
        ("retry", "verification_failed"),
        ("approve", "waiting_for_review"),
        ("resolve_dependency", "blocked_by_dependency"),
    ):
        target = resolve_next_status(
            action,
            current_status,
            assigned_agent_url="http://worker",
        )

        assert target == "assigned"
        assert can_transition_to(current_status, target) == (True, "")


def test_admission_refusal_is_persistable_and_removes_dispatch_eligibility():
    for status in ('todo', 'created', 'assigned', 'proposing', 'in_progress', 'delegated'):
        assert can_transition('request_review', status) == (True, '')
        assert can_transition_to(status, 'waiting_for_review') == (True, '')
        assert resolve_next_status('request_review', status) == 'waiting_for_review'
    assert can_autopilot_dispatch('waiting_for_review') is False
    for status in ('completed', 'failed', 'cancelled', 'verification_failed', 'reserved'):
        assert can_transition('request_review', status)[0] is False
        assert can_transition_to(status, 'waiting_for_review')[0] is False


def test_actual_sql_task_records_pre_execution_review_without_force(app):
    from agent.db_models import TaskDB
    from agent.repository import task_repo
    from agent.services.task_runtime_service import update_local_task_status

    task_repo.save(TaskDB(id='synthetic-admission-review', title='Synthetic task', status='todo'))
    update_local_task_status('synthetic-admission-review', 'waiting_for_review',
                            status_reason_code='native_autopilot_dispatch_blocked')
    task = task_repo.get_by_id('synthetic-admission-review')
    assert task.status == 'waiting_for_review'
    assert task.status_reason_code == 'native_autopilot_dispatch_blocked'
    assert not can_autopilot_dispatch(task.status)
