"""SEH-004: append-only genome/experiment/lineage store."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlmodel import SQLModel, create_engine

from agent import db_models  # noqa: F401 — registers all tables
from agent.repositories.harness_evolution import HarnessEvolutionStore
from ananta_contracts.harness_genome import (
    AgentTopology,
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


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'harness.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    return HarnessEvolutionStore(engine=lambda: engine, clock=lambda: 1000.0)


def test_append_genome_is_content_addressed_and_idempotent(store):
    value = genome()
    assert store.append_genome(value, model_requirements={"vram_gb": 16}) == value.genome_id
    assert store.append_genome(value) == value.genome_id
    stored = store.get_genome(value.genome_id)
    assert stored["code_hash"] == A and stored["model_requirements"] == {"vram_gb": 16}
    assert stored["canonical_json"] == value.to_json()


def test_lineage_is_fully_reconstructed(store):
    g1 = genome(context=ContextPolicy(budget_tokens=32768, reserved_completion_tokens=2048))
    g2 = genome(context=ContextPolicy(budget_tokens=65536, reserved_completion_tokens=2048))
    g3 = genome(context=ContextPolicy(budget_tokens=131072, reserved_completion_tokens=2048),
                topology=AgentTopology(pattern="delegated", delegation_depth=1, fan_out=2))
    for g in (g1, g2, g3):
        store.append_genome(g)
    store.append_mutation(parent_genome_id=g1.genome_id, child_genome_id=g2.genome_id,
                          operator="widen_context", hypothesis="more room lowers truncation")
    store.append_mutation(parent_genome_id=g2.genome_id, child_genome_id=g3.genome_id,
                          operator="add_delegation", hypothesis="delegation improves recall")
    chain = store.lineage(g3.genome_id)
    assert [item["genome_id"] for item in chain] == [g3.genome_id, g2.genome_id, g1.genome_id]
    assert store.lineage(g1.genome_id)[0]["genome_id"] == g1.genome_id


def test_lineage_unknown_genome_is_rejected(store):
    with pytest.raises(ValueError, match="harness_genome_unknown"):
        store.lineage("0" * 64)


def test_benchmark_value_is_traceable_to_full_context(store):
    value = genome()
    store.append_genome(value)
    experiment = store.start_experiment(genome_id=value.genome_id, task="planning", model="bonsai-27b",
                                        dataset_version="ds-1", code_commit="deadbeef", runtime_id="rt-1",
                                        baseline_genome_id=value.genome_id)
    run_id = store.record_benchmark_run(experiment_id=experiment, metrics={"task_correctness": 0.91,
                                                                          "token_input": 12000},
                                        gate_results=[{"gate_ref": "gate://security", "passed": True}],
                                        artifact_refs=["artifact://report"], trace_ref="trace-1")
    run = store.benchmark_runs(experiment)[0]
    assert run["run_id"] == run_id and run["genome_id"] == value.genome_id
    experiment_row = store.experiments(genome_id=value.genome_id)[0]
    assert (experiment_row["code_commit"], experiment_row["model"], experiment_row["dataset_version"],
            experiment_row["runtime_id"]) == ("deadbeef", "bonsai-27b", "ds-1", "rt-1")


def test_queries_filter_by_model_task_mutation_gate_and_pareto(store):
    base = genome()
    child = genome(context=ContextPolicy(budget_tokens=65536, reserved_completion_tokens=2048))
    store.append_genome(base)
    store.append_genome(child)
    store.append_mutation(parent_genome_id=base.genome_id, child_genome_id=child.genome_id,
                          operator="widen_context")
    for genome_id, model, task in ((base.genome_id, "m1", "planning"), (child.genome_id, "m2", "coding")):
        experiment = store.start_experiment(genome_id=genome_id, task=task, model=model, dataset_version="ds-1",
                                            code_commit="c1", runtime_id="rt-1")
        store.record_benchmark_run(experiment_id=experiment, metrics={"task_correctness": 0.5},
                                   gate_results=[{"gate_ref": "gate://security", "passed": True},
                                                 {"gate_ref": "gate://correctness",
                                                  "passed": genome_id == base.genome_id}])
    assert {row["model"] for row in store.experiments(model="m1")} == {"m1"}
    assert {row["task"] for row in store.experiments(task="coding")} == {"coding"}
    assert {row["genome_id"] for row in store.experiments(mutation_operator="widen_context")} == {child.genome_id}
    gated = store.experiments(gate="gate://correctness")
    assert {row["genome_id"] for row in gated} == {base.genome_id, child.genome_id}
    only = store.experiments(gate="gate://correctness")
    store.set_experiment_state(only[0]["experiment_id"], pareto_status="front", status="succeeded")
    assert {row["experiment_id"] for row in store.experiments(pareto_status="front")} == {only[0]["experiment_id"]}


def test_unknown_experiment_and_genome_are_rejected(store):
    with pytest.raises(ValueError, match="harness_genome_unknown"):
        store.start_experiment(genome_id="0" * 64, task="t", model="m", dataset_version="d", code_commit="c",
                               runtime_id="r")
    with pytest.raises(ValueError, match="harness_experiment_unknown"):
        store.record_benchmark_run(experiment_id="missing", metrics={"x": 1.0})
    with pytest.raises(ValueError, match="harness_experiment_status_invalid"):
        store.set_experiment_state("missing", status="nope")


@pytest.mark.parametrize("metrics", [{}, {"x": "high"}, {"x": True}, {"": 1.0}])
def test_invalid_metrics_are_rejected(store, metrics):
    value = genome()
    store.append_genome(value)
    experiment = store.start_experiment(genome_id=value.genome_id, task="t", model="m", dataset_version="d",
                                        code_commit="c", runtime_id="r")
    with pytest.raises(ValueError, match="harness_metrics_invalid"):
        store.record_benchmark_run(experiment_id=experiment, metrics=metrics)


def test_invalid_gate_result_is_rejected(store):
    value = genome()
    store.append_genome(value)
    experiment = store.start_experiment(genome_id=value.genome_id, task="t", model="m", dataset_version="d",
                                        code_commit="c", runtime_id="r")
    with pytest.raises(ValueError, match="harness_gate_result_invalid"):
        store.record_benchmark_run(experiment_id=experiment, metrics={"x": 1.0},
                                   gate_results=[{"gate_ref": "gate://x", "passed": "yes"}])


def test_semantic_change_creates_a_new_genome_row(store):
    base = genome()
    changed = replace(base, tools=ToolPolicy(allowed_tools=("repo.grep", "repo.list_files")))
    store.append_genome(base)
    store.append_genome(changed)
    assert base.genome_id != changed.genome_id
    assert store.get_genome(base.genome_id)["genome_id"] != store.get_genome(changed.genome_id)["genome_id"]
