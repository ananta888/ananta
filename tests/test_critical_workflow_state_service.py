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
