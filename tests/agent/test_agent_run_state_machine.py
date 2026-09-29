"""Tests for AgentRunStateMachine — COSMOS-002"""
from agent.services.agent_run_state_machine import (
    AgentRunState,
    AgentRunStateMachine,
    AgentRunStateError,
    TERMINAL_STATES,
)
import pytest


def test_create_starts_in_created_state():
    sm = AgentRunStateMachine()
    r = sm.create(goal_id="g1")
    assert r.state == AgentRunState.CREATED


def test_transition_created_to_queued():
    sm = AgentRunStateMachine()
    r = sm.create()
    r2 = sm.transition(r.run_id, AgentRunState.QUEUED)
    assert r2.state == AgentRunState.QUEUED


def test_transition_invalid_raises():
    sm = AgentRunStateMachine()
    r = sm.create()
    with pytest.raises(AgentRunStateError):
        sm.transition(r.run_id, AgentRunState.RUNNING)  # CREATED → RUNNING not allowed


def test_cancel_active_run():
    sm = AgentRunStateMachine()
    r = sm.create()
    sm.transition(r.run_id, AgentRunState.QUEUED)
    r2 = sm.cancel(r.run_id, reason="user request")
    assert r2.state == AgentRunState.CANCELLED


def test_cancel_terminal_raises():
    sm = AgentRunStateMachine()
    r = sm.create()
    sm.transition(r.run_id, AgentRunState.QUEUED)
    sm.cancel(r.run_id)
    with pytest.raises(AgentRunStateError):
        sm.cancel(r.run_id)


def test_fail_with_details():
    sm = AgentRunStateMachine()
    r = sm.create()
    sm.transition(r.run_id, AgentRunState.QUEUED)
    r2 = sm.fail(
        r.run_id,
        failed_at_step="planning",
        error_code="planning_failed",
        error_reason="no plan",
        recovery_options=["retry"],
    )
    assert r2.state == AgentRunState.FAILED
    assert r2.error_code == "planning_failed"
    assert r2.failed_at_step == "planning"
    assert "retry" in r2.recovery_options


def test_complete_from_verifying():
    sm = AgentRunStateMachine()
    r = sm.create()
    for s in [
        AgentRunState.QUEUED,
        AgentRunState.PLANNING,
        AgentRunState.RUNNING,
        AgentRunState.VERIFYING,
    ]:
        sm.transition(r.run_id, s)
    r2 = sm.complete(r.run_id, artifacts=["art-1", "art-2"])
    assert r2.state == AgentRunState.COMPLETED
    assert r2.artifacts == ["art-1", "art-2"]


def test_complete_from_running_raises():
    sm = AgentRunStateMachine()
    r = sm.create()
    for s in [AgentRunState.QUEUED, AgentRunState.PLANNING, AgentRunState.RUNNING]:
        sm.transition(r.run_id, s)
    with pytest.raises(AgentRunStateError):
        sm.complete(r.run_id)  # must go through VERIFYING


def test_retry_from_failed():
    sm = AgentRunStateMachine()
    r = sm.create()
    sm.transition(r.run_id, AgentRunState.QUEUED)
    sm.fail(r.run_id, error_code="timeout")
    r2 = sm.retry(r.run_id)
    assert r2.state == AgentRunState.QUEUED
    assert r2.error_code is None


def test_retry_from_non_failed_raises():
    sm = AgentRunStateMachine()
    r = sm.create()
    with pytest.raises(AgentRunStateError):
        sm.retry(r.run_id)


def test_state_history_tracked():
    sm = AgentRunStateMachine()
    r = sm.create()
    sm.transition(r.run_id, AgentRunState.QUEUED, reason="test")
    sm.transition(r.run_id, AgentRunState.PLANNING)
    assert len(r.state_history) == 3  # created + queued + planning
    assert r.state_history[1]["reason"] == "test"


def test_get_by_state_filters():
    sm = AgentRunStateMachine()
    r1 = sm.create()
    r2 = sm.create()
    sm.transition(r2.run_id, AgentRunState.QUEUED)
    created_runs = sm.get_by_state(AgentRunState.CREATED)
    assert r1.run_id in [x.run_id for x in created_runs]
    assert r2.run_id not in [x.run_id for x in created_runs]


def test_can_transition_allowed():
    sm = AgentRunStateMachine()
    r = sm.create()
    assert sm.can_transition(r.run_id, AgentRunState.QUEUED) is True


def test_can_transition_denied():
    sm = AgentRunStateMachine()
    r = sm.create()
    assert sm.can_transition(r.run_id, AgentRunState.RUNNING) is False


def test_is_terminal_completed():
    sm = AgentRunStateMachine()
    r = sm.create()
    for s in [
        AgentRunState.QUEUED,
        AgentRunState.PLANNING,
        AgentRunState.RUNNING,
        AgentRunState.VERIFYING,
    ]:
        sm.transition(r.run_id, s)
    sm.complete(r.run_id)
    assert r.is_terminal() is True


def test_is_terminal_failed():
    sm = AgentRunStateMachine()
    r = sm.create()
    sm.transition(r.run_id, AgentRunState.QUEUED)
    sm.fail(r.run_id)
    assert r.is_terminal() is True


def test_is_active_running():
    sm = AgentRunStateMachine()
    r = sm.create()
    for s in [AgentRunState.QUEUED, AgentRunState.PLANNING, AgentRunState.RUNNING]:
        sm.transition(r.run_id, s)
    assert r.is_active() is True


def test_artifacts_attached_on_complete():
    sm = AgentRunStateMachine()
    r = sm.create()
    for s in [
        AgentRunState.QUEUED,
        AgentRunState.PLANNING,
        AgentRunState.RUNNING,
        AgentRunState.VERIFYING,
    ]:
        sm.transition(r.run_id, s)
    sm.complete(r.run_id, artifacts=["art-xyz"])
    assert "art-xyz" in r.artifacts
