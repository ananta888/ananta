"""SEH-012: conflicts, authority preservation and mandatory fresh evaluation."""

from dataclasses import replace

import pytest

from agent.services.harness_benchmark import BenchmarkSuite, BenchmarkTask
from agent.services.harness_crossover import (
    CrossoverBenchmark,
    CrossoverError,
    CrossoverEvaluator,
    compose_genomes,
)
from agent.services.harness_gates import GateEngine, security_gate, threshold_gate
from agent.services.harness_runtime import baseline_genome


def parents():
    base = baseline_genome(
        code_hash="a" * 64,
        config_hash="b" * 64,
        provider_id="test",
        model_id="synthetic",
        gate_refs=("gate://security",),
        promotion_ref="gate://promotion",
        allowed_tools=("repo.read",),
    )
    left = replace(base, context=replace(base.context, budget_tokens=65536))
    right = replace(base, tools=replace(base.tools, action_fusion=True))
    return base, left, right


def compose(base, left, right):
    return compose_genomes(base=base, left=left, right=right)


def test_disjoint_changes_have_deterministic_identity_lineage_and_no_fitness():
    base, left, right = parents()
    proposal = compose(base, left, right)
    assert proposal.child.context == left.context
    assert proposal.child.tools == right.tools
    assert proposal.child.gates == base.gates
    assert proposal.child.parents == tuple(sorted((left.genome_id, right.genome_id)))
    assert proposal.child.created_from == base.genome_id
    assert proposal.child.mutation_operators == ("crossover",)
    assert proposal.changed_paths == ("context.budget_tokens", "tools.action_fusion")
    assert compose(base, right, left) == proposal
    assert "metrics" not in proposal.child.to_mapping()
    assert base.context.budget_tokens == 32768 and not base.tools.action_fusion


def test_disjoint_changes_within_one_section_are_merged():
    base, left, _ = parents()
    right = replace(base, context=replace(base.context, reserved_completion_tokens=4096))
    assert compose(base, left, right).child.context.reserved_completion_tokens == 4096


@pytest.mark.parametrize("budget", [65536, 131072])
def test_overlapping_fields_conflict_even_when_values_match(budget):
    base, left, _ = parents()
    right = replace(base, context=replace(base.context, budget_tokens=budget))
    with pytest.raises(CrossoverError, match="crossover_field_conflict:context.budget_tokens"):
        compose(base, left, right)


def test_unchanged_parent_is_rejected():
    base, left, _ = parents()
    with pytest.raises(CrossoverError, match="crossover_parent_unchanged"):
        compose(base, left, base)


@pytest.mark.parametrize(
    "change",
    [
        lambda g: replace(g, code_hash="c" * 64),
        lambda g: replace(g, config_hash="c" * 64),
        lambda g: replace(g, secret_refs=("vault://new",)),
        lambda g: replace(g, gates=replace(g.gates, gate_refs=("gate://other",))),
        lambda g: replace(g, gates=replace(g.gates, promotion_ref="gate://other")),
        lambda g: replace(g, memory=replace(g.memory, scopes=("admin",))),
        lambda g: replace(g, verification=replace(g.verification, require_evidence=False)),
        lambda g: replace(g, context=replace(g.context, evidence_preserving_reduction=False)),
        lambda g: replace(g, reducer=replace(g.reducer, preserve_evidence=False)),
        lambda g: replace(g, reducer=replace(g.reducer, verify_fallback=False)),
    ],
)
def test_immutable_authority_and_snapshot_changes_are_rejected(change):
    base, left, _ = parents()
    with pytest.raises(CrossoverError, match="crossover_immutable_change"):
        compose(base, left, change(base))


def test_code_edits_conflict_without_attempting_to_merge_opaque_hashes():
    base, _, _ = parents()
    with pytest.raises(CrossoverError, match="crossover_field_conflict:code_hash"):
        compose(base, replace(base, code_hash="c" * 64), replace(base, code_hash="d" * 64))


def test_tool_capability_expansion_is_rejected():
    base, left, _ = parents()
    right = replace(base, tools=replace(base.tools, allowed_tools=("repo.read", "shell.exec")))
    with pytest.raises(CrossoverError, match="crossover_tool_capability_expansion"):
        compose(base, left, right)


def test_individually_valid_parents_can_have_an_invalid_combination():
    base, _, _ = parents()
    left = replace(base, context=replace(base.context, budget_tokens=4096))
    right = replace(base, context=replace(base.context, reserved_completion_tokens=8192))
    left.assert_valid()
    right.assert_valid()
    with pytest.raises(CrossoverError, match="crossover_combination_invalid:harness_context_reserved_exceeds_budget"):
        compose(base, left, right)


class BenchmarkDouble:
    def __init__(self):
        self.calls = []
        self.metrics = {"task_correctness": 0.9, "token_input": 1000}
        self.inputs = {"security_ok": True, "task_correctness": 1.0}
        self.wrong_genome = None
        self.wrong_suite = None
        self.completed = ("explore", "holdout")

    def evaluate(self, genome, *, suite_id):
        self.calls.append((genome, suite_id))
        return CrossoverBenchmark(
            self.wrong_genome or genome.genome_id,
            self.wrong_suite or suite_id,
            self.metrics,
            self.inputs,
            self.completed,
        )


def evaluator(benchmark):
    suite = BenchmarkSuite.create(
        version="synthetic-v1",
        tasks=[
            BenchmarkTask("explore", "coding", "synthetic", required_metrics=("task_correctness", "token_input")),
            BenchmarkTask("holdout", "security", "synthetic", holdout=True),
        ],
    )
    gates = GateEngine(
        [security_gate("security:scope", "security_ok"), threshold_gate("quality", "task_correctness", 0.8)]
    )
    return CrossoverEvaluator(benchmark=benchmark, suite=suite, gates=gates)


def evaluate(service):
    base, left, right = parents()
    return service.compose_and_evaluate(base=base, left=left, right=right)


def test_entire_child_is_benchmarked_and_regated_before_selection():
    benchmark = BenchmarkDouble()
    result = evaluate(evaluator(benchmark))
    assert benchmark.calls == [(result.proposal.child, result.benchmark.suite_id)]
    assert result.eligible_for_selection
    assert len(result.gates.outcomes) == 2
    benchmark.metrics["task_correctness"] = 0.1
    assert result.benchmark.metrics["task_correctness"] == 0.9
    with pytest.raises(TypeError):
        result.benchmark.metrics["task_correctness"] = 1.0


@pytest.mark.parametrize("failure", ["security", "quality"])
def test_high_parent_or_shadow_metrics_cannot_override_child_gate_failure(failure):
    benchmark = BenchmarkDouble()
    if failure == "security":
        benchmark.inputs["security_ok"] = False
    else:
        benchmark.metrics["task_correctness"] = 0.5
    result = evaluate(evaluator(benchmark))
    assert not result.eligible_for_selection
    assert result.gates.disqualified


@pytest.mark.parametrize("binding", ["wrong_genome", "wrong_suite"])
def test_observation_from_another_child_or_suite_is_rejected(binding):
    benchmark = BenchmarkDouble()
    setattr(benchmark, binding, "unrelated")
    with pytest.raises(CrossoverError, match="crossover_benchmark_binding_mismatch"):
        evaluate(evaluator(benchmark))


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "0.9"])
def test_invalid_metrics_fail_closed(value):
    benchmark = BenchmarkDouble()
    benchmark.metrics["task_correctness"] = value
    with pytest.raises(CrossoverError, match="crossover_metrics_invalid"):
        evaluate(evaluator(benchmark))


def test_missing_metrics_and_missing_hard_gates_are_rejected():
    benchmark = BenchmarkDouble()
    benchmark.metrics.pop("token_input")
    with pytest.raises(CrossoverError, match="crossover_required_metrics_missing"):
        evaluate(evaluator(benchmark))
    suite = BenchmarkSuite.create(version="v1", tasks=[BenchmarkTask("t", "coding", "synthetic")])
    with pytest.raises(CrossoverError, match="crossover_hard_gates_required"):
        CrossoverEvaluator(benchmark=benchmark, suite=suite, gates=GateEngine([]))


@pytest.mark.parametrize(
    "completed", [("explore",), ("explore", "holdout", "extra"), ("explore", "holdout", "holdout")]
)
def test_incomplete_or_incorrect_suite_coverage_is_rejected(completed):
    benchmark = BenchmarkDouble()
    benchmark.completed = completed
    with pytest.raises(CrossoverError, match="crossover_benchmark_tasks_mismatch"):
        evaluate(evaluator(benchmark))


def test_empty_or_tampered_suite_is_rejected():
    benchmark = BenchmarkDouble()
    gates = GateEngine([security_gate("security:scope", "security_ok")])
    suite = BenchmarkSuite.create(version="v1", tasks=[])
    with pytest.raises(CrossoverError, match="crossover_benchmark_suite_empty"):
        CrossoverEvaluator(benchmark=benchmark, suite=suite, gates=gates)
    suite = BenchmarkSuite.create(version="v1", tasks=[BenchmarkTask("t", "coding", "synthetic")])
    with pytest.raises(CrossoverError, match="crossover_benchmark_suite_invalid"):
        CrossoverEvaluator(benchmark=benchmark, suite=replace(suite, suite_id="tampered"), gates=gates)


def test_quality_and_resources_are_required_even_without_declared_task_metrics():
    benchmark = BenchmarkDouble()
    benchmark.metrics.pop("token_input")
    benchmark.completed = ("t",)
    suite = BenchmarkSuite.create(version="v1", tasks=[BenchmarkTask("t", "coding", "synthetic")])
    service = CrossoverEvaluator(
        benchmark=benchmark, suite=suite, gates=GateEngine([security_gate("security:scope", "security_ok")])
    )
    with pytest.raises(CrossoverError, match="crossover_quality_and_resource_metrics_required"):
        evaluate(service)


def test_empty_observation_is_rejected():
    benchmark = BenchmarkDouble()
    benchmark.metrics.clear()
    with pytest.raises(CrossoverError, match="crossover_metrics_missing"):
        evaluate(evaluator(benchmark))


def test_benchmark_error_does_not_produce_an_eligible_candidate():
    class FailedBenchmark:
        def evaluate(self, genome, *, suite_id):
            raise TimeoutError("synthetic timeout")

    with pytest.raises(TimeoutError, match="synthetic timeout"):
        evaluate(evaluator(FailedBenchmark()))


def test_conflict_does_not_dispatch_a_benchmark():
    benchmark = BenchmarkDouble()
    service = evaluator(benchmark)
    base, left, _ = parents()
    with pytest.raises(CrossoverError):
        service.compose_and_evaluate(base=base, left=left, right=left)
    assert not benchmark.calls
