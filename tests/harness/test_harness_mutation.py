"""SEH-009: mutation engine types, budgets, code-patch boundaries, determinism."""

from __future__ import annotations

from dataclasses import replace

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
        code_hash=A,
        config_hash=B,
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
        engine.mutate(
            genome(
                prompt=PromptStrategy(
                    system_ref="prompt://system", user_ref="prompt://user", few_shot_refs=("demo://x",)
                )
            ),
            operator_id="add_few_shot",
            params={"ref": "demo://x"},
        )


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
            genome(), module="agent/services/mutation_gate_service.py", patch_digest="c" * 64
        )
    with pytest.raises(MutationError, match="mutation_patch_digest_invalid"):
        engine.code_patch(genome(), module="agent/services/retrieval_service.py", patch_digest="short")


def test_plan_is_deterministic_for_a_seed():
    first = MutationEngine().plan(genome(), seed=42, count=5)
    second = MutationEngine().plan(genome(), seed=42, count=5)
    assert mutation_plan_digest(first) == mutation_plan_digest(second)
    assert all(result.parent_genome_id == genome().genome_id for result in first)


def test_context_mutation_preserves_every_other_context_policy_field():
    base = genome(
        context=ContextPolicy(
            budget_tokens=32768,
            reserved_completion_tokens=2048,
            reserved_tool_tokens=4096,
            compaction_threshold=0.5,
            observation_retention="last_20",
            retrieval_window_tokens=8192,
        )
    )
    result = MutationEngine().mutate(base, operator_id="widen_context")
    assert result.child_genome.context == replace(base.context, budget_tokens=65536)
    assert result.diff == {"context.budget_tokens": [32768, 65536]}


@pytest.mark.parametrize("value", [True, "65536", 65536.5])
def test_context_mutation_does_not_silently_coerce_untyped_values(value):
    with pytest.raises(MutationError, match="mutation_context_budget_invalid"):
        MutationEngine().mutate(genome(), operator_id="widen_context", params={"budget_tokens": value})


def test_mutation_engines_do_not_share_operator_registration_or_budget_dicts():
    first, second = MutationEngine(), MutationEngine()
    first.operators.clear()
    assert "widen_context" in second.operators
    budgets = {"context_policy": 1}
    engine = MutationEngine(budget=budgets)
    budgets["context_policy"] = 10
    engine.mutate(genome(), operator_id="widen_context")
    with pytest.raises(MutationError, match="mutation_budget_exceeded"):
        engine.mutate(genome(), operator_id="widen_context")


@pytest.mark.parametrize(
    "module",
    [
        "agent/services/harness_gates.py",
        "agent/services/harness_promotion.py",
        "worker/harness/sandbox.py",
        "ananta_contracts/harness_search.py",
    ],
)
def test_protected_governance_code_stays_immutable_even_if_misregistered(module):
    with pytest.raises(MutationError, match="mutation_patch_module_immutable"):
        MutationEngine(allowed_patch_modules=frozenset({module})).code_patch(
            genome(), module=module, patch_digest="c" * 64
        )


def test_registered_operator_cannot_mutate_authority_or_expand_tools():
    for operator in (
        lambda g, _: replace(g, gates=replace(g.gates, promotion_ref="gate://other")),
        lambda g, _: replace(g, tools=replace(g.tools, allowed_tools=("shell.exec",))),
    ):
        engine = MutationEngine(operators={"unsafe": ("structural", operator)})
        with pytest.raises(MutationError, match="mutation_immutable_change|mutation_tool_capability_expansion"):
            engine.mutate(genome(), operator_id="unsafe")


@pytest.mark.parametrize("module", ["/tmp/extension.py", "agent/services/../services/harness_gates.py", "./module.py"])
def test_patch_zone_registration_cannot_hide_path_traversal(module):
    with pytest.raises(MutationError, match="mutation_patch_module_path_invalid"):
        MutationEngine(allowed_patch_modules=frozenset({module})).code_patch(
            genome(), module=module, patch_digest="c" * 64
        )
