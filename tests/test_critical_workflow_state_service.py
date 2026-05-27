from __future__ import annotations

import time

import pytest

from agent.services.critical_workflow_state_service import WorkflowTransitionError, get_critical_workflow_state_service


def test_critical_workflow_transitions_and_replay_for_evolution() -> None:
    service = get_critical_workflow_state_service()
    record = service.initialize("evolution_proposal", state="review_required", now=100.0)
    record = service.transition(record, workflow_type="evolution_proposal", to_state="approved", reason="review_approve", now=101.0)
    record = service.transition(record, workflow_type="evolution_proposal", to_state="apply_requested", reason="apply_requested", now=102.0)
    record = service.transition(record, workflow_type="evolution_proposal", to_state="apply_in_progress", reason="started", now=103.0)
    record = service.transition(record, workflow_type="evolution_proposal", to_state="apply_prepared", reason="prepared", now=104.0)
    replay = service.replay(record, workflow_type="evolution_proposal")

    assert record["state"] == "apply_prepared"
    assert replay["valid"] is True
    assert replay["state_path"][-1] == "apply_prepared"


def test_critical_workflow_rejects_invalid_transition() -> None:
    service = get_critical_workflow_state_service()
    record = service.initialize("evolution_proposal", state="review_required")
    with pytest.raises(WorkflowTransitionError) as exc:
        service.transition(record, workflow_type="evolution_proposal", to_state="apply_in_progress", reason="invalid")
    assert exc.value.code == "workflow_invalid_transition"


def test_critical_workflow_timeout_detection_and_bounded_recovery() -> None:
    service = get_critical_workflow_state_service()
    now = time.time()
    record = service.initialize("evolution_proposal", state="apply_in_progress", now=now - 600)
    record["last_transition_at"] = now - 600
    inspected = service.inspect_timeout(record, workflow_type="evolution_proposal", now=now)
    recovered = service.handle_timeout(record, workflow_type="evolution_proposal", reason="apply_stuck", now=now)

    assert inspected["stuck"] is True
    assert recovered["state"] == "blocked"
    assert recovered["recovery_attempts"] == 1


def test_critical_workflow_enforces_named_guards() -> None:
    service = get_critical_workflow_state_service()
    record = service.initialize("evolution_proposal", state="approved")
    with pytest.raises(WorkflowTransitionError) as exc:
        service.transition(
            record,
            workflow_type="evolution_proposal",
            to_state="apply_requested",
            reason="guarded_apply",
            guards={"approval_bound": False},
        )
    assert exc.value.code == "workflow_guard_blocked"
    assert exc.value.details.get("guard") == "approval_bound"


def test_repair_workflow_replay_covers_valid_critical_path() -> None:
    service = get_critical_workflow_state_service()
    record = service.initialize("repair_execution", state="detected", now=10.0)
    record = service.transition(record, workflow_type="repair_execution", to_state="diagnosing", reason="start_diag", now=11.0)
    record = service.transition(record, workflow_type="repair_execution", to_state="proposing", reason="diag_done", now=12.0)
    record = service.transition(record, workflow_type="repair_execution", to_state="approval_required", reason="needs_review", now=13.0)
    record = service.transition(record, workflow_type="repair_execution", to_state="executing", reason="approved", now=14.0)
    record = service.transition(record, workflow_type="repair_execution", to_state="verifying", reason="exec_done", now=15.0)
    record = service.transition(record, workflow_type="repair_execution", to_state="succeeded", reason="verify_passed", now=16.0)
    replay = service.replay(record, workflow_type="repair_execution")

    assert replay["valid"] is True
    assert replay["terminal_reached"] is True
    assert replay["state_path"][-1] == "succeeded"
