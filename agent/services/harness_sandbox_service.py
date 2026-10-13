"""Hub admission and evidence ingress for delegated offline Harness runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from agent.services.harness_benchmark import BenchmarkRegistry, BenchmarkSuite
from agent.services.harness_crossover import CrossoverBenchmark
from agent.services.harness_gates import GateEngine, GateReport
from agent.services.hub_evidence_gate_service import (
    EvidenceGateOutcome,
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
)
from ananta_contracts.harness_genome import content_digest
from ananta_contracts.harness_sandbox import HarnessSandboxManifest


class HarnessSandboxWorkerPort(Protocol):
    """Transport for an already authorized Hub task and exact assignment lease."""

    def execute(self, manifest: HarnessSandboxManifest, assignment: Mapping) -> dict: ...


@dataclass(frozen=True, slots=True)
class HarnessSandboxEvaluation:
    evidence: EvidenceGateOutcome
    benchmark: CrossoverBenchmark | None
    gates: GateReport | None


class HarnessSandboxService:
    """Keep reservation, result acceptance and gating on the Hub side."""

    def __init__(
        self,
        *,
        evidence: HubEvidenceGateService,
        worker: HarnessSandboxWorkerPort,
        suite: BenchmarkSuite,
        gates: GateEngine,
    ):
        if not suite.tasks or BenchmarkSuite.create(version=suite.version, tasks=suite.tasks) != suite:
            raise ValueError("harness_sandbox_suite_invalid")
        if not any(gate.hard for gate in gates.gates):
            raise ValueError("harness_sandbox_hard_gates_required")
        self._evidence, self._worker, self._suite, self._gates = evidence, worker, suite, gates

    def evaluate(self, manifest: HarnessSandboxManifest, *, request: EvidenceGateRequest) -> HarnessSandboxEvaluation:
        manifest.assert_valid()
        if (
            manifest.repository_revision != request.repository_revision
            or manifest.suite_id != self._suite.suite_id
            or set(manifest.task_ids) != {task.task_id for task in self._suite.tasks}
        ):
            raise ValueError("harness_sandbox_admission_binding_mismatch")
        # Caller strings never become evidence: immutable sources and the run
        # are admitted/reserved by the existing Hub registry before dispatch.
        bound_request = replace(
            request,
            input_digest=manifest.digest,
            execution_profile_digest=manifest.limits.digest,
            environment_digest=content_digest({"image": manifest.image, "network": "none", "gpu": False}),
            sources=(
                EvidenceGateSourceAdmission(
                    "harness_genome",
                    manifest.genome.genome_id,
                    manifest.genome.genome_id,
                    manifest.limits.digest,
                    request.synthetic,
                ),
                EvidenceGateSourceAdmission(
                    "harness_benchmark",
                    content_digest({"revision": manifest.repository_revision, "suite_id": manifest.suite_id}),
                    manifest.source_digest,
                    manifest.limits.digest,
                    request.synthetic,
                ),
            ),
        )
        observation = None
        report = None

        def execute(projection):
            nonlocal observation, report
            result = self._worker.execute(manifest, projection)
            if (
                not isinstance(result, Mapping)
                or result.get("assignment") != projection
                or result.get("manifest_digest") != manifest.digest
                or result.get("genome_id") != manifest.genome.genome_id
                or result.get("suite_id") != manifest.suite_id
            ):
                raise ValueError("harness_sandbox_worker_binding_mismatch")
            if result.get("passed") is not True:
                return {
                    "passed": False,
                    "reason_code": "harness_sandbox_worker_failed",
                    "manifest_digest": manifest.digest,
                }
            raw = result.get("observation")
            if not isinstance(raw, Mapping):
                raise ValueError("harness_sandbox_observation_invalid")
            observation = CrossoverBenchmark(
                manifest.genome.genome_id,
                manifest.suite_id,
                raw.get("metrics", {}),
                raw.get("gate_inputs", {}),
                tuple(raw.get("completed_task_ids", ())),
            )
            completed = observation.completed_task_ids
            if len(set(completed)) != len(completed) or set(completed) != set(manifest.task_ids):
                raise ValueError("harness_sandbox_benchmark_incomplete")
            required = {metric for task in self._suite.tasks for metric in task.required_metrics}
            if not required <= observation.metrics.keys() or not BenchmarkRegistry().measures_quality_and_resources(
                observation.metrics
            ):
                raise ValueError("harness_sandbox_metrics_incomplete")
            if any(raw.get(key, 0) != 0 for key in ("api_cost", "model_tokens", "tool_calls")):
                raise ValueError("harness_sandbox_offline_budget_violation")
            report = self._gates.evaluate(dict(observation.gate_inputs) | dict(observation.metrics))
            return {
                "passed": not report.disqualified,
                "manifest_digest": manifest.digest,
                "genome_id": observation.genome_id,
                "suite_id": observation.suite_id,
                "metrics": dict(observation.metrics),
                "gates": report.to_mapping(),
                "completed_task_ids": list(completed),
            }

        outcome = self._evidence.execute(bound_request, execute)
        return HarnessSandboxEvaluation(outcome, observation, report)
