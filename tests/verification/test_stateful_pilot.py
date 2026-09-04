from __future__ import annotations

import pytest
from hypothesis import find, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from agent.services.agent_run_state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    AgentRunState,
    AgentRunStateError,
    AgentRunStateMachine,
)
from worker.verification.pilot_targets import permission_subset_is_monotone

REFERENCE_TRANSITIONS: dict[AgentRunState, frozenset[AgentRunState]] = {
    AgentRunState.CREATED: frozenset({AgentRunState.QUEUED, AgentRunState.CANCELLED}),
    AgentRunState.QUEUED: frozenset({AgentRunState.PLANNING, AgentRunState.CANCELLED, AgentRunState.FAILED}),
    AgentRunState.PLANNING: frozenset(
        {
            AgentRunState.WAITING_FOR_CONTEXT,
            AgentRunState.WAITING_FOR_APPROVAL,
            AgentRunState.RUNNING,
            AgentRunState.FAILED,
            AgentRunState.CANCELLED,
        }
    ),
    AgentRunState.WAITING_FOR_CONTEXT: frozenset(
        {AgentRunState.PLANNING, AgentRunState.RUNNING, AgentRunState.FAILED, AgentRunState.CANCELLED}
    ),
    AgentRunState.WAITING_FOR_APPROVAL: frozenset(
        {AgentRunState.RUNNING, AgentRunState.CANCELLED, AgentRunState.FAILED}
    ),
    AgentRunState.RUNNING: frozenset(
        {AgentRunState.VERIFYING, AgentRunState.WAITING_FOR_APPROVAL, AgentRunState.FAILED, AgentRunState.CANCELLED}
    ),
    AgentRunState.VERIFYING: frozenset({AgentRunState.COMPLETED, AgentRunState.FAILED, AgentRunState.RUNNING}),
    AgentRunState.COMPLETED: frozenset(),
    AgentRunState.FAILED: frozenset({AgentRunState.QUEUED}),
    AgentRunState.CANCELLED: frozenset(),
}


class AgentLifecycleModel(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.machine = AgentRunStateMachine()
        self.record = self.machine.create(correlation_id="property-state-machine")

    @rule(next_state=st.sampled_from(tuple(AgentRunState)))
    def choose_transition(self, next_state: AgentRunState) -> None:
        source = self.record.state
        history_before = tuple(dict(item) for item in self.record.state_history)
        if next_state in REFERENCE_TRANSITIONS[source]:
            self.machine.transition(self.record.run_id, next_state, reason="generated-property-step")
            assert self.record.state is next_state
            assert len(self.record.state_history) == len(history_before) + 1
            return
        try:
            self.machine.transition(self.record.run_id, next_state, reason="generated-invalid-step")
        except AgentRunStateError:
            pass
        else:
            raise AssertionError(f"invalid transition accepted: {source.value}->{next_state.value}")
        assert self.record.state is source
        assert tuple(self.record.state_history) == history_before

    @invariant()
    def history_matches_current_state(self) -> None:
        assert self.record.state_history[-1]["state"] == self.record.state.value


class PermissionGrowthModel(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.required = {"read"}
        self.granted: set[str] = set()
        self.was_allowed = False

    @rule(capability=st.sampled_from(["read", "write", "admin"]))
    def grant(self, capability: str) -> None:
        self.granted.add(capability)
        allowed = permission_subset_is_monotone(self.required, self.granted)
        if self.was_allowed:
            assert allowed
        self.was_allowed = allowed

    @invariant()
    def decision_matches_subset_policy(self) -> None:
        assert permission_subset_is_monotone(self.required, self.granted) == (self.required <= self.granted)


TestAgentLifecycle = AgentLifecycleModel.TestCase
TestPermissionGrowth = PermissionGrowthModel.TestCase
TestAgentLifecycle.settings = settings(
    max_examples=50,
    stateful_step_count=30,
    deadline=None,
    database=None,
    derandomize=True,
)
TestPermissionGrowth.settings = TestAgentLifecycle.settings


def test_reference_model_covers_every_production_transition() -> None:
    assert set(REFERENCE_TRANSITIONS) == set(AgentRunState)
    assert REFERENCE_TRANSITIONS == ALLOWED_TRANSITIONS
    assert set(TERMINAL_STATES) - {AgentRunState.FAILED} == {
        state for state, targets in REFERENCE_TRANSITIONS.items() if not targets
    }


def test_every_allowed_and_forbidden_transition_matches_reference_model() -> None:
    for source in AgentRunState:
        for target in AgentRunState:
            machine = AgentRunStateMachine()
            record = machine.create(correlation_id=f"coverage-{source.value}-{target.value}")
            record.state = source
            record.state_history.append({"state": source.value, "timestamp": 0.0, "reason": "coverage-setup"})
            if target in REFERENCE_TRANSITIONS[source]:
                assert machine.transition(record.run_id, target).state is target
            else:
                before = tuple(dict(item) for item in record.state_history)
                with pytest.raises(AgentRunStateError):
                    machine.transition(record.run_id, target)
                assert record.state is source
                assert tuple(record.state_history) == before


def test_hypothesis_shrinks_a_mutated_transition_to_the_offending_action() -> None:
    mutated = dict(REFERENCE_TRANSITIONS)
    mutated[AgentRunState.COMPLETED] = frozenset({AgentRunState.RUNNING})
    found = find(
        st.tuples(st.sampled_from(tuple(AgentRunState)), st.sampled_from(tuple(AgentRunState))),
        lambda transition: (transition[1] in mutated[transition[0]])
        != (transition[1] in REFERENCE_TRANSITIONS[transition[0]]),
        settings=settings(max_examples=200, deadline=None, database=None, derandomize=True),
    )
    assert found == (AgentRunState.COMPLETED, AgentRunState.RUNNING)
