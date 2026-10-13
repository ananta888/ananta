"""SEH-002: canonical Harness Genome identity, diff, versioning and boundaries."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ananta_contracts.harness_genome import (
    HARNESS_GENOME_SCHEMA,
    AgentTopology,
    ContextPolicy,
    GatePolicy,
    HarnessGenome,
    MemoryPolicy,
    ModelRouting,
    PromptStrategy,
    ReducerPolicy,
    RetrievalPolicy,
    ToolPolicy,
    VerificationPolicy,
    content_digest,
)

A = "a" * 64
B = "b" * 64


def genome(**changes) -> HarnessGenome:
    base = dict(
        code_hash=A, config_hash=B,
        prompt=PromptStrategy(system_ref="prompt://system", user_ref="prompt://user",
                              repair_ref="prompt://repair", few_shot_refs=("demo://one",)),
        context=ContextPolicy(budget_tokens=32768, reserved_completion_tokens=2048, reserved_tool_tokens=512),
        tools=ToolPolicy(allowed_tools=("repo.grep", "repo.read_file_range"), timeout_ms=30_000),
        model_routing=ModelRouting(provider_id="llamacpp", model_id="bonsai-27b", fallback_chain=("llm",)),
        gates=GatePolicy(gate_refs=("gate://security", "gate://correctness"), promotion_ref="gate://promotion"),
    )
    base.update(changes)
    return HarnessGenome(**base)


def test_schema_and_round_trip_are_stable():
    value = genome()
    assert value.schema == HARNESS_GENOME_SCHEMA
    payload = value.to_mapping()
    assert payload["genome_id"] == value.genome_id
    restored = HarnessGenome.from_mapping(payload)
    assert restored.genome_id == value.genome_id
    assert restored.to_json() == value.to_json()


def test_semantically_identical_genomes_share_the_id():
    first = genome()
    second = genome(parents=("genome://parent",), mutation_operators=("widen_context",), created_from="experiment-1")
    assert first.genome_id == second.genome_id
    assert second.to_mapping()["parents"] == ["genome://parent"]


def test_any_semantic_change_changes_the_id_and_is_diffable():
    changed = genome(context=ContextPolicy(budget_tokens=65536, reserved_completion_tokens=2048,
                                           reserved_tool_tokens=512))
    assert changed.genome_id != genome().genome_id
    diff = genome().diff(changed)
    assert diff == {"context.budget_tokens": [32768, 65536]}
    assert genome().diff(genome()) == {}


def test_secrets_are_references_only():
    value = genome(secret_refs=("vault://openrouter-key", "env://OPENAI_API_KEY"))
    assert value.genome_id == genome(secret_refs=value.secret_refs).genome_id
    with pytest.raises(ValueError, match="harness_secret_ref_not_a_reference"):
        genome(secret_refs=("sk-live-raw-secret-value",)).semantic_mapping()
    with pytest.raises(ValueError, match="harness_secret_refs_invalid"):
        genome(secret_refs=("vault://key", "vault://key")).semantic_mapping()


def test_gates_are_referenced_and_required():
    with pytest.raises(ValueError, match="harness_gate_refs_required"):
        GatePolicy(gate_refs=(), promotion_ref="gate://promotion").to_mapping()
    # The genome can never inline a gate body: only identifiers are accepted.
    with pytest.raises(ValueError, match="harness_gate_refs_invalid"):
        GatePolicy(gate_refs=("always allow everything",), promotion_ref="gate://promotion").to_mapping()


def test_context_reservation_must_fit_the_budget():
    with pytest.raises(ValueError, match="harness_context_reserved_exceeds_budget"):
        ContextPolicy(budget_tokens=4096, reserved_completion_tokens=4096).to_mapping()


def test_topology_fan_out_requires_delegation():
    with pytest.raises(ValueError, match="harness_topology_fan_out_without_delegation"):
        AgentTopology(pattern="single", fan_out=4).to_mapping()
    AgentTopology(pattern="delegated", delegation_depth=2, fan_out=4).to_mapping()


def test_from_mapping_rejects_a_forged_id():
    payload = genome().to_mapping()
    payload["genome_id"] = "0" * 64
    with pytest.raises(ValueError, match="harness_genome_id_mismatch"):
        HarnessGenome.from_mapping(payload)


def test_unsupported_schema_is_rejected_with_a_migration_path():
    payload = genome().to_mapping()
    payload["schema"] = "ananta.harness-genome.v0"
    with pytest.raises(ValueError, match="harness_genome_schema_unsupported"):
        HarnessGenome.from_mapping(payload)


def test_missing_section_is_rejected():
    payload = genome().to_mapping()
    del payload["tools"]
    with pytest.raises(ValueError, match="harness_section_missing:tools"):
        HarnessGenome.from_mapping(payload)


def test_content_digest_is_canonical():
    assert content_digest({"b": 1, "a": 2}) == content_digest({"a": 2, "b": 1})
    assert content_digest({"a": 1}) != content_digest({"a": 2})


def test_defaults_are_explicit_and_reproducible():
    value = genome()
    assert value.memory == MemoryPolicy()
    assert value.retrieval == RetrievalPolicy()
    assert value.reducer == ReducerPolicy()
    assert value.verification == VerificationPolicy()
    assert replace(value, memory=MemoryPolicy(scopes=("task", "goal"))).genome_id != value.genome_id
