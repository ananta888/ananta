from __future__ import annotations

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from agent.services.agent_run_state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    AgentRunState,
    AgentRunStateError,
    AgentRunStateMachine,
)
from worker.verification.pilot_targets import permission_subset_is_monotone


class AgentLifecycleModel(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.machine = AgentRunStateMachine()
        self.record = self.machine.create(correlation_id="property-state-machine")

    @precondition(lambda self: self.record.state not in TERMINAL_STATES)
    @rule()
    def choose_transition(self) -> None:
        allowed = sorted(ALLOWED_TRANSITIONS[self.record.state], key=lambda item: item.value)
        if allowed:
            self.machine.transition(self.record.run_id, allowed[0], reason="automatic-property-step")

    @precondition(lambda self: self.record.state in TERMINAL_STATES)
    @rule()
    def terminal_reopen_is_rejected(self) -> None:
        for target in AgentRunState:
            if target in ALLOWED_TRANSITIONS[self.record.state]:
                continue
            try:
                self.machine.transition(self.record.run_id, target)
            except AgentRunStateError:
                pass
            else:
                raise AssertionError("terminal run reopened")

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
