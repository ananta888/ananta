"""CodeCompass-grounded retrieval hypotheses stay bounded, honest and deterministic."""

import pytest

from agent.services.harness_hypotheses import (
    CodeCompassSignalCollector,
    CodeCompassSignals,
    HarnessHypothesisGenerator,
    HarnessTelemetry,
)
from agent.services.harness_mutation import MutationEngine, MutationError
from tests.harness.test_harness_sandbox import manifest

GENERATOR = HarnessHypothesisGenerator()
SIGNALS = CodeCompassSignals(
    indexed_files=120, indexed_edges=400, architecture_modules=("agent/services", "agent/routes", "worker/core")
)


def telemetry(**kwargs):
    return HarnessTelemetry(truncation_events=1, codecompass=CodeCompassSignals(**kwargs))


def propose(telemetry_value, genome=None):
    genome = genome or manifest()[0].genome
    return GENERATOR.propose(genome, telemetry_value, context_budgets=(65536,), model_context_limit=131072)


def test_signals_validate_bounds_and_dedupe_modules():
    assert CodeCompassSignals(indexed_files=0).present is False
    with pytest.raises(ValueError, match="signals_invalid"):
        CodeCompassSignals(indexed_files=-1)
    with pytest.raises(ValueError, match="signals_invalid"):
        CodeCompassSignals(indexed_edges=10_000_001)
    signals = CodeCompassSignals(indexed_files=3, architecture_modules=("a/b", "a/b", "c/d"))
    assert signals.architecture_modules == ("a/b", "c/d")
    with pytest.raises(ValueError, match="signals_invalid"):
        CodeCompassSignals(architecture_modules=("x" * 201,))


def test_telemetry_rejects_foreign_signal_types():
    with pytest.raises(ValueError, match="telemetry_invalid"):
        HarnessTelemetry(codecompass={"indexed_files": 5})
    assert HarnessTelemetry().codecompass is None


def test_generator_stays_inert_without_codecompass_signals():
    ideas = propose(HarnessTelemetry(truncation_events=1, safe_fusion_opportunities=2))
    assert {idea.operator_id for idea in ideas} == {"widen_context"}
    assert propose(HarnessTelemetry(codecompass=CodeCompassSignals())) == ()


def test_generator_grounds_retrieval_ideas_in_graph_coverage():
    import json as _json

    ideas = propose(telemetry(indexed_files=120, indexed_edges=400, architecture_modules=("agent/services",)))
    by_operator = {idea.operator_id: idea for idea in ideas}
    assert set(by_operator) == {"widen_context", "boost_graph_retrieval", "deepen_retrieval"}
    boost = by_operator["boost_graph_retrieval"]
    assert _json.loads(boost.params_json) == {"graph_weight": 0.5}
    assert "120 files" in boost.rationale and "1 modules" in boost.rationale
    assert boost.priority == 150000
    deepen = by_operator["deepen_retrieval"]
    assert _json.loads(deepen.params_json) == {"top_k": 16, "candidate_budget": 128}
    assert "400 relations" in deepen.rationale
    assert deepen.priority == 140000


def test_generator_skips_saturated_retrieval_policy():
    genome = manifest()[0].genome
    mapping = genome.to_mapping()
    mapping.pop("genome_id", None)
    mapping["retrieval"] = {
        **genome.retrieval.to_mapping(),
        "graph_weight": 1.0,
        "top_k": 1000,
        "candidate_budget": 100000,
    }
    saturated = type(genome).from_mapping(mapping)
    ideas = propose(telemetry(indexed_files=120, indexed_edges=400), genome=saturated)
    assert {idea.operator_id for idea in ideas} == {"widen_context"}


def test_retrieval_mutations_apply_and_fail_closed():
    genome = manifest()[0].genome
    engine = MutationEngine()
    boosted = engine.mutate(
        genome, operator_id="boost_graph_retrieval", params={"graph_weight": 0.5}, rationale="graph coverage"
    )
    assert boosted.child_genome.retrieval.graph_weight == 0.5
    assert boosted.mutation_type == "retrieval_policy"
    deepened = engine.mutate(
        boosted.child_genome, operator_id="deepen_retrieval", params={"top_k": 16, "candidate_budget": 128}
    )
    assert (deepened.child_genome.retrieval.top_k, deepened.child_genome.retrieval.candidate_budget) == (16, 128)

    def expect_graph_weight_invalid(params):
        with pytest.raises(MutationError, match="graph_weight_invalid"):
            MutationEngine().mutate(genome, operator_id="boost_graph_retrieval", params=params)

    for params in ({}, {"graph_weight": 0.0}, {"graph_weight": 1.5}, {"graph_weight": "high"}):
        expect_graph_weight_invalid(params)
    with pytest.raises(MutationError, match="retrieval_depth_invalid"):
        MutationEngine().mutate(genome, operator_id="deepen_retrieval", params={"top_k": 1})
    with pytest.raises(MutationError, match="retrieval_depth_invalid"):
        MutationEngine().mutate(genome, operator_id="deepen_retrieval", params={"top_k": "many"})


def test_retrieval_budget_bounds_repeated_mutations():
    genome = manifest()[0].genome
    engine = MutationEngine()
    weight = 0.0
    for _ in range(3):
        weight = round(weight + 0.25, 4)
        genome = engine.mutate(
            genome, operator_id="boost_graph_retrieval", params={"graph_weight": weight}
        ).child_genome
    with pytest.raises(MutationError, match="mutation_budget_exceeded:retrieval_policy"):
        engine.mutate(genome, operator_id="boost_graph_retrieval", params={"graph_weight": 1.0})


class _Store:
    def __init__(self, payload=None, error=False):
        self._payload, self._error = payload, error

    def load(self):
        if self._error:
            raise OSError("store unavailable")
        return self._payload


def test_collector_counts_files_edges_and_ranks_modules_bounded():
    nodes = [{"path": f"agent/services/mod_{index}.py"} for index in range(40)]
    nodes += [{"path": f"worker/core/core_{index}.py"} for index in range(10)]
    nodes += [{"kind": "symbol", "path": ""}, "garbage"]
    collector = CodeCompassSignalCollector(_Store({"nodes": nodes, "edges": [{"a": 1}] * 25}))
    signals = collector.collect()
    assert signals.indexed_files == 50
    assert signals.indexed_edges == 25
    assert signals.architecture_modules == ("agent/services", "worker/core")
    assert signals.present is True


def test_collector_fails_soft_on_unreadable_or_malformed_stores():
    assert CodeCompassSignalCollector(_Store(error=True)).collect() is None
    assert CodeCompassSignalCollector(_Store({"nodes": "nope"})).collect() is None
    assert CodeCompassSignalCollector(_Store(None)).collect() is None
