"""SEH-003: harness runtime extension points and immutable invariants."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.harness_runtime import (
    HarnessInvariantViolation,
    HarnessRuntime,
    StrategyRegistry,
    baseline_genome,
    build_default_strategy_registry,
)
from ananta_contracts.harness_genome import ContextPolicy, ToolPolicy

A = "a" * 64
B = "b" * 64
GATES = ("gate://security", "gate://correctness", "gate://promotion")


class GateRegistry:
    def __init__(self, registered=GATES):
        self._registered = frozenset(registered)

    def is_registered(self, gate_ref: str) -> bool:
        return gate_ref in self._registered


def baseline(**changes):
    base = dict(code_hash=A, config_hash=B, provider_id="llamacpp", model_id="bonsai-27b",
                gate_refs=("gate://security", "gate://correctness"), promotion_ref="gate://promotion",
                allowed_tools=("repo.grep", "repo.read_list_files"))
    base.update(changes)
    return baseline_genome(**base)


def runtime(genome, *, authorized_tools=frozenset(), gates=GATES, strategies=None):
    return HarnessRuntime(genome, strategies=strategies or build_default_strategy_registry(),
                          gates=GateRegistry(gates), authorized_tools=frozenset(authorized_tools))


def test_baseline_reproduces_current_behaviour():
    value = runtime(baseline(), authorized_tools=("repo.grep",)).describe()
    assert value.strategies == {"context": "fixed_reservation", "tools": "contract", "memory": "policy_gateway",
                                "delegation": "hub_only", "verification": "hub_gate", "stop_retry": "task_terminal",
                                "reducer": "none"}
    assert value.immutable_gates == ("gate://security", "gate://correctness")
    assert value.promotion_ref == "gate://promotion"


def test_genome_cannot_widen_the_tool_capability():
    value = runtime(baseline(), authorized_tools=("repo.grep",))
    assert value.effective_tools() == ("repo.grep",)
    assert "repo.read_list_files" not in value.effective_tools()


def test_unregistered_gate_is_a_violation():
    with pytest.raises(HarnessInvariantViolation, match="harness_gate_unregistered"):
        runtime(baseline(gate_refs=("gate://security", "gate://unknown")))


def test_unregistered_promotion_gate_is_a_violation():
    with pytest.raises(HarnessInvariantViolation, match="harness_gate_unregistered"):
        runtime(baseline(promotion_ref="gate://unknown"))


def test_unsupported_strategy_is_rejected_not_silently_replaced():
    value = baseline()
    value = replace(value, verification=replace(value.verification, strategy="self_attested"))
    with pytest.raises(HarnessInvariantViolation, match="harness_strategy_unsupported:verification"):
        runtime(value)


def test_delegation_strategy_is_unsupported_until_registered():
    value = baseline()
    value = replace(value, topology=replace(value.topology, pattern="delegated", delegation_depth=1, fan_out=2))
    with pytest.raises(HarnessInvariantViolation, match="harness_strategy_unsupported:delegation"):
        runtime(value)


def test_registry_rejects_unknown_family_and_duplicates():
    registry = StrategyRegistry()
    with pytest.raises(ValueError, match="harness_strategy_family_unknown"):
        registry.register("magic", "x", lambda: None)
    registry.register("verification", "hub_gate", lambda: None)
    with pytest.raises(ValueError, match="harness_strategy_duplicate"):
        registry.register("verification", "hub_gate", lambda: None)
    with pytest.raises(ValueError, match="harness_strategy_family_unknown"):
        runtime(baseline()).strategy("magic")


def test_reservation_reports_available_input():
    value = runtime(baseline(), authorized_tools=("repo.grep",))
    reservation = value.reservation()
    assert (reservation.budget_tokens, reservation.reserved_completion_tokens) == (32768, 2048)
    assert reservation.available_input_tokens == 32768 - 2048


def test_genome_hash_and_tools_are_independent_of_runtime_binding():
    genome = baseline()
    a = runtime(genome, authorized_tools=("repo.grep",))
    b = runtime(genome, authorized_tools=("repo.grep", "repo.read_list_files"))
    assert a.genome.genome_id == b.genome.genome_id
    assert a.effective_tools() != b.effective_tools()


def test_context_policy_budget_is_carried_into_the_reservation():
    genome = replace(baseline(), context=ContextPolicy(budget_tokens=65536, reserved_completion_tokens=4096,
                                                       reserved_tool_tokens=1024))
    reservation = runtime(genome, authorized_tools=("repo.grep",)).reservation()
    assert reservation.available_input_tokens == 65536 - 4096 - 1024


def test_tool_policy_is_required_for_the_runtime_binding():
    genome = replace(baseline(), tools=ToolPolicy(allowed_tools=()))
    assert runtime(genome).effective_tools() == ()
