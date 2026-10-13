"""Conservative three-way composition of harness improvements (SEH-012).

Composition only produces a proposal. The Hub supplies the benchmark authority
and immutable gates for fresh evaluation; this module never dispatches workers,
promotes genomes, or issues evidence identities. Code-patch composition requires
a separate patch-aware adapter and is deliberately rejected here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from agent.services.harness_benchmark import BenchmarkRegistry, BenchmarkSuite
from agent.services.harness_gates import GateEngine, GateReport
from ananta_contracts.harness_genome import HarnessGenome
from ananta_contracts.harness_invariants import IMMUTABLE_GENOME_PATHS


class CrossoverError(ValueError):
    """Incompatible parents or an unbound fresh evaluation."""


@dataclass(frozen=True, slots=True)
class CrossoverProposal:
    base_genome_id: str
    child: HarnessGenome
    changed_paths: tuple[str, ...]


def compose_genomes(*, base: HarnessGenome, left: HarnessGenome, right: HarnessGenome) -> CrossoverProposal:
    """Merge disjoint field changes against an explicit common baseline.

    Even identical edits to the same field conflict: independence has not been
    established. Tuple fields are atomic, and authority/snapshot changes are
    rejected. Provenance records both parents without inheriting fitness.
    """
    for genome in (base, left, right):
        genome.assert_valid()
    left_diff, right_diff = base.diff(left), base.diff(right)
    if not left_diff or not right_diff:
        raise CrossoverError("crossover_parent_unchanged")
    conflicts = sorted(left_diff.keys() & right_diff.keys())
    if conflicts:
        raise CrossoverError(f"crossover_field_conflict:{','.join(conflicts)}")
    changes = left_diff | right_diff
    forbidden = sorted(changes.keys() & IMMUTABLE_GENOME_PATHS)
    if forbidden:
        raise CrossoverError(f"crossover_immutable_change:{','.join(forbidden)}")
    for parent in (left, right):
        if not set(parent.tools.allowed_tools) <= set(base.tools.allowed_tools):
            raise CrossoverError("crossover_tool_capability_expansion")

    payload = base.semantic_mapping()
    for path, (_, value) in changes.items():
        section, field = path.split(".", 1)
        payload[section][field] = value
    payload.update(
        parents=sorted((left.genome_id, right.genome_id)), mutation_operators=["crossover"], created_from=base.genome_id
    )
    try:
        child = HarnessGenome.from_mapping(payload).assert_valid()
    except (ValueError, TypeError) as exc:
        raise CrossoverError(f"crossover_combination_invalid:{exc}") from None
    return CrossoverProposal(base.genome_id, child, tuple(sorted(changes)))


@dataclass(frozen=True, slots=True)
class CrossoverBenchmark:
    """Fresh technical observation, never production release evidence by itself."""

    genome_id: str
    suite_id: str
    metrics: Mapping[str, float]
    gate_inputs: Mapping[str, object]
    completed_task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.metrics:
            raise CrossoverError("crossover_metrics_missing")
        for metric, value in self.metrics.items():
            if (
                not isinstance(metric, str)
                or not metric
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise CrossoverError("crossover_metrics_invalid")
        # Keep a callback's mutable dictionaries from changing accepted results.
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "gate_inputs", MappingProxyType(dict(self.gate_inputs)))
        object.__setattr__(self, "completed_task_ids", tuple(self.completed_task_ids))


class CrossoverBenchmarkPort(Protocol):
    """Hub-owned authority: evaluate the child over the complete specified suite.

    Implementations use the Hub task/lease/evidence and isolated worker paths.
    Holdouts stay with this authority and are never passed to a mutation engine.
    """

    def evaluate(self, genome: HarnessGenome, *, suite_id: str) -> CrossoverBenchmark: ...


@dataclass(frozen=True, slots=True)
class EvaluatedCrossover:
    proposal: CrossoverProposal
    benchmark: CrossoverBenchmark
    gates: GateReport

    @property
    def eligible_for_selection(self) -> bool:
        return not self.gates.disqualified


class CrossoverEvaluator:
    """Evaluate a newly composed child through injected Hub authorities (DIP)."""

    def __init__(self, *, benchmark: CrossoverBenchmarkPort, suite: BenchmarkSuite, gates: GateEngine):
        if not suite.tasks:
            raise CrossoverError("crossover_benchmark_suite_empty")
        if BenchmarkSuite.create(version=suite.version, tasks=suite.tasks) != suite:
            raise CrossoverError("crossover_benchmark_suite_invalid")
        if not any(gate.hard for gate in gates.gates):
            raise CrossoverError("crossover_hard_gates_required")
        self._benchmark = benchmark
        self._suite = suite
        self._gates = gates

    def compose_and_evaluate(
        self, *, base: HarnessGenome, left: HarnessGenome, right: HarnessGenome
    ) -> EvaluatedCrossover:
        proposal = compose_genomes(base=base, left=left, right=right)
        # No parent observations or performance values are accepted as input.
        observation = self._benchmark.evaluate(proposal.child, suite_id=self._suite.suite_id)
        if observation.genome_id != proposal.child.genome_id or observation.suite_id != self._suite.suite_id:
            raise CrossoverError("crossover_benchmark_binding_mismatch")
        completed = observation.completed_task_ids
        if len(set(completed)) != len(completed) or set(completed) != {task.task_id for task in self._suite.tasks}:
            raise CrossoverError("crossover_benchmark_tasks_mismatch")
        required = {metric for task in self._suite.tasks for metric in task.required_metrics}
        if not required <= observation.metrics.keys():
            raise CrossoverError("crossover_required_metrics_missing")
        if not BenchmarkRegistry().measures_quality_and_resources(observation.metrics):
            raise CrossoverError("crossover_quality_and_resource_metrics_required")
        # Gate inputs cannot shadow the fresh benchmark's metric values.
        report = self._gates.evaluate(dict(observation.gate_inputs) | dict(observation.metrics))
        return EvaluatedCrossover(proposal, observation, report)
