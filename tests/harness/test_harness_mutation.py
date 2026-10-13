"""SEH-009: mutation engine types, budgets, code-patch boundaries, determinism."""

from __future__ import annotations

import pytest

from agent.services.harness_mutation import MutationEngine, MutationError, mutation_plan_digest
from ananta_contracts.harness_genome import (
    ContextPolicy,
    GatePolicy,
    HarnessGenome,
    ModelRouting,
    PromptStrategy,
    ToolPolicy,
)

A = "a" * 64
B = "b" * 64


def genome(**changes) -> HarnessGenome:
    base = dict(
        code_hash=A, config_hash=B,
        prompt=PromptStrategy(system_ref="prompt://system", user_ref="prompt://user"),
        context=ContextPolicy(budget_tokens=32768, reserved_completion_tokens=2048),
        tools=ToolPolicy(allowed_tools=("repo.grep",)),
        model_routing=ModelRouting(provider_id="llamacpp", model_id="bonsai-27b"),
        gates=GatePolicy(gate_refs=("gate://security",), promotion_ref="gate://promotion"),
    )
    base.update(changes)
    return HarnessGenome(**base)


def test_parametric_context_mutation_reports_type_parent_and_diff():
    result = MutationEngine().mutate(genome(), operator_id="widen_context", rationale="more room")
    assert result.mutation_type == "context_policy" and result.rationale == "more room"
    assert result.parent_genome_id == genome().genome_id
    assert result.child_genome.context.budget_tokens == 65536
    assert result.diff == {"context.budget_tokens": [32768, 65536]}


def test_unknown_operator_and_no_change_are_rejected():
    with pytest.raises(MutationError, match="mutation_operator_unknown"):
        MutationEngine().mutate(genome(), operator_id="nope")
    with pytest.raises(MutationError, match="mutation_produced_no_change"):
        engine = MutationEngine()
        engine.mutate(genome(), operator_id="add_few_shot", params={"ref": "demo://x"})
        engine.mutate(genome(prompt=PromptStrategy(system_ref="prompt://system", user_ref="prompt://user",
                                                   few_shot_refs=("demo://x",))),
                      operator_id="add_few_shot", params={"ref": "demo://x"})


def test_invalid_mutation_params_are_rejected_before_execution():
    with pytest.raises(MutationError, match="mutation_context_budget_invalid"):
        MutationEngine().mutate(genome(), operator_id="widen_context", params={"budget_tokens": 10})
    with pytest.raises(MutationError, match="mutation_few_shot_ref_invalid"):
        MutationEngine().mutate(genome(), operator_id="add_few_shot", params={})


def test_mutation_budget_is_enforced():
    engine = MutationEngine(budget={"context_policy": 1})
    engine.mutate(genome(), operator_id="widen_context")
    with pytest.raises(MutationError, match="mutation_budget_exceeded:context_policy"):
        engine.mutate(genome(), operator_id="widen_context")
    assert engine.spent() == {"context_policy": 1}


def test_code_patch_respects_the_module_boundary():
    engine = MutationEngine(allowed_patch_modules=frozenset({"agent/services/retrieval_service.py"}))
    result = engine.code_patch(genome(), module="agent/services/retrieval_service.py", patch_digest="c" * 64)
    assert result.mutation_type == "code_patch" and result.child_genome.code_hash == "c" * 64
    with pytest.raises(MutationError, match="mutation_patch_module_not_allowed"):
        engine.code_patch(genome(), module="agent/services/other.py", patch_digest="c" * 64)
    with pytest.raises(MutationError, match="mutation_patch_module_immutable"):
        MutationEngine(allowed_patch_modules=frozenset({"agent/services/mutation_gate_service.py"})).code_patch(
            genome(), module="agent/services/mutation_gate_service.py", patch_digest="c" * 64)
    with pytest.raises(MutationError, match="mutation_patch_digest_invalid"):
        engine.code_patch(genome(), module="agent/services/retrieval_service.py", patch_digest="short")


def test_plan_is_deterministic_for_a_seed():
    first = MutationEngine().plan(genome(), seed=42, count=5)
    second = MutationEngine().plan(genome(), seed=42, count=5)
    assert mutation_plan_digest(first) == mutation_plan_digest(second)
    assert all(result.parent_genome_id == genome().genome_id for result in first)
