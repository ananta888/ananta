from __future__ import annotations

from agent.game.aegis_flow import AegisFlow
from agent.game.aegis_hub import AegisHub, DelegationRequest


def test_aegis_flow_successful_path_to_completed() -> None:
    flow = AegisFlow()
    state = "goal"
    for event in (
        "plan_created",
        "task_ready",
        "action_started",
        "action_completed",
        "verification_passed",
        "artifact_recorded",
    ):
        transition = flow.advance(state=state, event=event)
        assert transition.allowed is True
        state = transition.next_state
    assert state == "completed"


def test_aegis_hub_blocks_worker_to_worker_orchestration() -> None:
    hub = AegisHub(orchestrator_role="hub")
    decision = hub.evaluate_delegation(
        DelegationRequest(owner_role="worker_planner", delegate_role="worker_executor", goal_id="g1", task_id="t1")
    )
    assert decision.allowed is False
    assert decision.reason_code == "delegation_owner_must_be_hub"


def test_aegis_flow_retry_path_on_failed_verification() -> None:
    flow = AegisFlow()
    transition = flow.advance(state="verification", event="verification_failed")
    assert transition.allowed is True
    assert transition.next_state == "retry"
    retry_back = flow.advance(state="retry", event="retry_action")
    assert retry_back.next_state == "action"


def test_aegis_flow_rollback_path_on_policy_violation() -> None:
    flow = AegisFlow()
    transition = flow.advance(state="verification", event="policy_violation")
    assert transition.allowed is True
    assert transition.next_state == "rollback"
