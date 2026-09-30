"""Deterministic production release gate for workflow runtimes.

The gate consumes normalized evidence emitted by runtime-specific test
harnesses.  It never executes a runtime itself, which keeps framework imports
outside the Hub domain service and makes the evidence verifier reusable by
runtime selection and rollout code.

This module is the stable public entry point and owns the gate evaluation.
Configuration loading, proof-source mapping, and the artifact verifier and
admission adapter live in the ``release_gate_*`` sibling modules and are
re-exported here unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.workflow_evaluation_service import (
    WorkflowEvaluationSuite,
    load_workflow_evaluation_suite,
)
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.conformance import CONFORMANCE_REPORT_SCHEMA, WorkflowConformanceEvaluator
from agent.services.workflow_runtime.execution_plan import EXECUTION_PLAN_SCHEMA
from agent.services.workflow_runtime.reference_workflows import (
    REFERENCE_WORKFLOW_CATALOG_SCHEMA,
    ReferenceWorkflow,
    load_reference_workflows,
)
from agent.services.workflow_runtime.release_evidence import (
    EVIDENCE_BUILD_ID_ENV,
    EVIDENCE_CHAIN_GENESIS,
    EVIDENCE_COMMAND_HASH_ENV,
    EVIDENCE_COMMAND_ID_ENV,
    EVIDENCE_CONTRACT_HASH_ENV,
    EVIDENCE_PATH_ENV,
    EVIDENCE_REVISION_ENV,
    EVIDENCE_RUNTIME_ID_ENV,
    PROOF_CATEGORIES,
    RuntimeEvidenceBinding,
    RuntimeEvidenceSink,
    RuntimeRunEvidence,
    VerificationCommandResult,
    assert_evidence_chain,
    load_runtime_release_evidence,
    load_runtime_release_evidence_jsonl,
    record_runtime_release_evidence,
    release_verification_command_hash,
)
from agent.services.workflow_runtime.release_gate_admission import (
    WorkflowRuntimeGateEvidenceVerifier,
    WorkflowRuntimeReleaseAdmission,
)
from agent.services.workflow_runtime.release_gate_config import (  # noqa: F401 - public re-export
    DEFAULT_WORKFLOW_RELEASE_GATE_PATH,
    WORKFLOW_RELEASE_GATE_SCHEMA,
    WORKFLOW_RELEASE_RESULT_SCHEMA,
    ReleaseVerificationCommand,
    RuntimeReleaseRequirement,
    WorkflowReleaseGateConfig,
    _safe_argv,
    _string_set,
    _string_tuple,
    load_workflow_release_gate_config,
    workflow_runtime_contract_hash,
)
from agent.services.workflow_runtime.release_gate_proof_sources import (  # noqa: F401 - public re-export
    _APPROVAL_SOURCE_EVENTS,
    _DURABLE_CHECKPOINT_SOURCE_EVENTS,
    _DURABLE_RECOVERY_SOURCE_EVENTS,
    _HUB_CHECKPOINT_SOURCE_EVENTS,
    _HUB_RECOVERY_SOURCE_EVENTS,
    _LEDGER_SOURCE_EVENTS,
    _observed_proof_sources,
)

_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, order=True)
class ReleaseDeviation:
    runtime_id: str
    scenario_id: str
    iteration: int
    code: str
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "runtime_id": self.runtime_id,
            "scenario_id": self.scenario_id,
            "iteration": self.iteration,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class WorkflowRuntimeReleaseResult:
    config: WorkflowReleaseGateConfig
    contract_hash: str
    status: str
    capability_matrix: Mapping[str, tuple[str, ...]]
    scenario_matrix: Mapping[str, Mapping[str, Mapping[str, Any]]]
    invariant_matrix: Mapping[str, Mapping[str, str]]
    invariant_evidence: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]]
    deviations: tuple[ReleaseDeviation, ...]
    verification_results: tuple[VerificationCommandResult, ...]
    evidence_digest: str
    evidence_bindings: Mapping[str, Any]
    versions: Mapping[str, Any]

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": WORKFLOW_RELEASE_RESULT_SCHEMA,
            "artifact_version": self.config.artifact_version,
            "gate_id": self.config.gate_id,
            "gate_version": self.config.gate_version,
            "status": self.status,
            "contract_hash": self.contract_hash,
            "evidence_digest": self.evidence_digest,
            "evidence_bindings": deepcopy(dict(self.evidence_bindings)),
            "versions": dict(self.versions),
            "capability_matrix": {key: list(value) for key, value in sorted(self.capability_matrix.items())},
            "scenario_matrix": {
                runtime_id: {scenario_id: dict(values) for scenario_id, values in sorted(scenarios.items())}
                for runtime_id, scenarios in sorted(self.scenario_matrix.items())
            },
            "invariant_matrix": {
                key: dict(sorted(value.items())) for key, value in sorted(self.invariant_matrix.items())
            },
            "invariant_evidence": {
                runtime_id: {
                    category: [deepcopy(dict(item)) for item in records]
                    for category, records in sorted(categories.items())
                }
                for runtime_id, categories in sorted(self.invariant_evidence.items())
            },
            "deviations": [item.to_dict() for item in self.deviations],
            "verification_commands": [item.to_dict() for item in self.verification_results],
            "reproduce": [
                {"command_id": command.command_id, "argv": list(command.argv)}
                for command in self.config.verification_commands
            ],
        }
        if include_hash:
            payload["artifact_hash"] = sha256_json(payload)
        return payload


class WorkflowRuntimeReleaseGate:
    """Evaluate normalized runtime evidence against all declared capabilities."""

    def __init__(
        self,
        *,
        config: WorkflowReleaseGateConfig | None = None,
        scenarios: Sequence[ReferenceWorkflow] | None = None,
        suite: WorkflowEvaluationSuite | None = None,
        conformance: WorkflowConformanceEvaluator | None = None,
    ) -> None:
        self._scenarios = tuple(scenarios or load_reference_workflows())
        self._config = config or load_workflow_release_gate_config(scenarios=self._scenarios)
        self._suite = suite or load_workflow_evaluation_suite()
        if self._config.evaluation_suite_version != self._suite.suite_version:
            raise ValueError("workflow_release_gate_evaluation_suite_version_mismatch")
        self._conformance = conformance or WorkflowConformanceEvaluator()
        self._contract_hash = workflow_runtime_contract_hash(self._config, self._suite)

    @property
    def contract_hash(self) -> str:
        return self._contract_hash

    @property
    def config(self) -> WorkflowReleaseGateConfig:
        return self._config

    def evaluate(
        self,
        evidence: Sequence[RuntimeRunEvidence],
        verification_results: Sequence[VerificationCommandResult],
    ) -> WorkflowRuntimeReleaseResult:
        records = tuple(evidence)
        for record in records:
            record.assert_valid()
        deviations: list[ReleaseDeviation] = []
        command_results = self._verify_commands(tuple(verification_results), deviations)
        evidence_bindings = self._verify_evidence_bindings(records, command_results, deviations)
        scenario_by_id = {scenario.scenario_id: scenario for scenario in self._scenarios}
        scenario_matrix: dict[str, dict[str, dict[str, Any]]] = {}
        invariant_matrix: dict[str, dict[str, str]] = {}
        invariant_evidence: dict[str, dict[str, tuple[dict[str, Any], ...]]] = {}
        capability_matrix: dict[str, tuple[str, ...]] = {}

        for requirement in self._config.runtimes:
            runtime_records = tuple(item for item in records if item.runtime_id == requirement.runtime_id)
            capability_matrix[requirement.runtime_id] = tuple(sorted(requirement.capabilities))
            runtime_scenarios: dict[str, dict[str, Any]] = {}
            category_applicable = {category: False for category in PROOF_CATEGORIES}
            category_failed = {category: False for category in PROOF_CATEGORIES}
            for scenario in self._scenarios:
                selected = tuple(item for item in runtime_records if item.scenario_id == scenario.scenario_id)
                required = scenario.scenario_id in requirement.required_scenarios
                if not required:
                    if selected:
                        deviations.append(
                            ReleaseDeviation(
                                requirement.runtime_id,
                                scenario.scenario_id,
                                0,
                                "incompatible_scenario_has_success_evidence",
                            )
                        )
                    runtime_scenarios[scenario.scenario_id] = {
                        "status": "incompatible",
                        "durable": False,
                        "iterations": 0,
                        "reason_code": "runtime_scenario_incompatible",
                    }
                    continue
                scenario_deviations = self._evaluate_scenario(
                    requirement,
                    scenario,
                    selected,
                    category_applicable=category_applicable,
                    category_failed=category_failed,
                )
                deviations.extend(scenario_deviations)
                runtime_scenarios[scenario.scenario_id] = {
                    "status": "failed" if scenario_deviations else "passed",
                    "durable": scenario.scenario_id in requirement.durable_scenarios,
                    "iterations": len(selected),
                    "reason_code": scenario_deviations[0].code if scenario_deviations else "",
                }
            deviations.extend(
                self._capability_coverage_deviations(
                    requirement,
                    runtime_records,
                    scenario_by_id,
                    category_applicable,
                    category_failed,
                )
            )
            invariant_matrix[requirement.runtime_id] = {
                category: (
                    "failed"
                    if category_failed[category]
                    else "passed"
                    if category_applicable[category]
                    else "not_applicable"
                )
                for category in PROOF_CATEGORIES
            }
            invariant_evidence[requirement.runtime_id] = {
                category: tuple(
                    {
                        "command_id": record.command_id,
                        "iteration": record.iteration,
                        "run_id": record.run_id,
                        "scenario_id": record.scenario_id,
                        "sources": list(_observed_proof_sources(record, category)),
                    }
                    for record in sorted(
                        runtime_records,
                        key=lambda value: (value.scenario_id, value.iteration, value.run_id),
                    )
                    if record.proofs[category] == "passed"
                    and _observed_proof_sources(record, category)
                )
                for category in PROOF_CATEGORIES
            }
            scenario_matrix[requirement.runtime_id] = runtime_scenarios

        known_runtimes = {item.runtime_id for item in self._config.runtimes}
        known_scenarios = set(scenario_by_id)
        for record in records:
            if record.runtime_id not in known_runtimes:
                deviations.append(
                    ReleaseDeviation(
                        record.runtime_id,
                        record.scenario_id,
                        record.iteration,
                        "undeclared_runtime_evidence",
                    )
                )
            elif record.scenario_id not in known_scenarios:
                deviations.append(
                    ReleaseDeviation(
                        record.runtime_id,
                        record.scenario_id,
                        record.iteration,
                        "undeclared_scenario_evidence",
                    )
                )
        ordered_deviations = tuple(sorted(set(deviations)))
        evidence_digest = sha256_json(
            [
                item.to_dict()
                for item in sorted(
                    records,
                    key=lambda value: (value.command_id, value.chain_index),
                )
            ]
        )
        return WorkflowRuntimeReleaseResult(
            config=self._config,
            contract_hash=self._contract_hash,
            status="passed" if not ordered_deviations else "failed",
            capability_matrix=capability_matrix,
            scenario_matrix=scenario_matrix,
            invariant_matrix=invariant_matrix,
            invariant_evidence=invariant_evidence,
            deviations=ordered_deviations,
            verification_results=command_results,
            evidence_digest=evidence_digest,
            evidence_bindings=evidence_bindings,
            versions={
                "execution_plan_schema": EXECUTION_PLAN_SCHEMA,
                "reference_catalog_schema": REFERENCE_WORKFLOW_CATALOG_SCHEMA,
                "reference_catalog_version": self._config.reference_catalog_version,
                "reference_catalog_sha256": self._suite.catalog_sha256,
                "conformance_report_schema": CONFORMANCE_REPORT_SCHEMA,
                "evaluation_suite_version": self._suite.suite_version,
                "evaluation_suite_hash": self._suite.suite_hash,
                "runtimes": {
                    item.runtime_id: item.runtime_version
                    for item in sorted(self._config.runtimes, key=lambda value: value.runtime_id)
                },
            },
        )

    def _verify_commands(
        self,
        results: tuple[VerificationCommandResult, ...],
        deviations: list[ReleaseDeviation],
    ) -> tuple[VerificationCommandResult, ...]:
        by_id = {item.command_id: item for item in results}
        if len(by_id) != len(results):
            deviations.append(ReleaseDeviation("", "", 0, "verification_command_duplicate"))
        ordered: list[VerificationCommandResult] = []
        for configured in self._config.verification_commands:
            result = by_id.get(configured.command_id)
            if result is None:
                deviations.append(ReleaseDeviation("", "", 0, "verification_command_missing", (configured.command_id,)))
                continue
            ordered.append(result)
            if result.argv != configured.argv:
                deviations.append(ReleaseDeviation("", "", 0, "verification_command_drift", (configured.command_id,)))
            if not result.passed:
                deviations.append(ReleaseDeviation("", "", 0, "verification_command_failed", (configured.command_id,)))
        unknown = set(by_id) - {item.command_id for item in self._config.verification_commands}
        for command_id in sorted(unknown):
            deviations.append(ReleaseDeviation("", "", 0, "verification_command_unknown", (command_id,)))
        return tuple(ordered)

    def _verify_evidence_bindings(
        self,
        records: tuple[RuntimeRunEvidence, ...],
        command_results: tuple[VerificationCommandResult, ...],
        deviations: list[ReleaseDeviation],
    ) -> dict[str, Any]:
        configured = {command.command_id: command for command in self._config.verification_commands}
        results = {result.command_id: result for result in command_results}
        run_ids: set[tuple[str, str]] = set()
        for record in records:
            command = configured.get(record.command_id)
            if command is None or record.runtime_id not in command.evidence_runtime_ids:
                deviations.append(
                    ReleaseDeviation(
                        record.runtime_id,
                        record.scenario_id,
                        record.iteration,
                        "evidence_command_binding_mismatch",
                        (record.command_id,),
                    )
                )
                continue
            expected_hash = release_verification_command_hash(command, self._contract_hash)
            result = results.get(record.command_id)
            if record.command_hash != expected_hash:
                deviations.append(
                    ReleaseDeviation(
                        record.runtime_id,
                        record.scenario_id,
                        record.iteration,
                        "evidence_command_hash_mismatch",
                    )
                )
            if result is None or record.revision != result.revision or record.build_id != result.build_id:
                deviations.append(
                    ReleaseDeviation(
                        record.runtime_id,
                        record.scenario_id,
                        record.iteration,
                        "evidence_build_binding_mismatch",
                    )
                )
            run_key = (record.command_id, record.run_id)
            if run_key in run_ids:
                deviations.append(
                    ReleaseDeviation(
                        record.runtime_id,
                        record.scenario_id,
                        record.iteration,
                        "evidence_run_id_duplicate",
                    )
                )
            run_ids.add(run_key)

        for command_id, command in configured.items():
            result = results.get(command_id)
            command_records = tuple(
                sorted(
                    (record for record in records if record.command_id == command_id),
                    key=lambda item: item.chain_index,
                )
            )
            if result is None:
                continue
            if command_records:
                try:
                    assert_evidence_chain(command_records, expected_command_id=command_id)
                except ValueError as exc:
                    deviations.append(
                        ReleaseDeviation(
                            "",
                            "",
                            0,
                            "verification_command_evidence_chain_invalid",
                            (command_id, str(exc)),
                        )
                    )
            if result.command_hash != release_verification_command_hash(command, self._contract_hash):
                deviations.append(
                    ReleaseDeviation("", "", 0, "verification_command_hash_mismatch", (command_id,))
                )
            if command.evidence_runtime_ids and not command_records:
                deviations.append(
                    ReleaseDeviation("", "", 0, "verification_command_evidence_missing", (command_id,))
                )
            if result.evidence_records != len(command_records):
                deviations.append(
                    ReleaseDeviation("", "", 0, "verification_command_evidence_count_mismatch", (command_id,))
                )
            expected_head = command_records[-1].record_hash if command_records else ""
            if result.evidence_chain_head != expected_head:
                deviations.append(
                    ReleaseDeviation("", "", 0, "verification_command_chain_head_mismatch", (command_id,))
                )

        revisions = sorted({record.revision for record in records} | {item.revision for item in command_results})
        build_ids = sorted({record.build_id for record in records} | {item.build_id for item in command_results})
        if len(revisions) != 1:
            deviations.append(ReleaseDeviation("", "", 0, "release_revision_binding_inconsistent"))
        if len(build_ids) != 1:
            deviations.append(ReleaseDeviation("", "", 0, "release_build_binding_inconsistent"))
        return {
            "revision": revisions[0] if len(revisions) == 1 else "",
            "build_id": build_ids[0] if len(build_ids) == 1 else "",
            "commands": {
                command_id: {
                    "command_hash": result.command_hash,
                    "evidence_records": result.evidence_records,
                    "evidence_chain_head": result.evidence_chain_head,
                }
                for command_id, result in sorted(results.items())
            },
        }

    def _evaluate_scenario(
        self,
        requirement: RuntimeReleaseRequirement,
        scenario: ReferenceWorkflow,
        records: tuple[RuntimeRunEvidence, ...],
        *,
        category_applicable: dict[str, bool],
        category_failed: dict[str, bool],
    ) -> tuple[ReleaseDeviation, ...]:
        deviations: list[ReleaseDeviation] = []
        iterations = [record.iteration for record in records]
        ordered_iterations = sorted(iterations)
        expected_iterations = list(range(1, (max(ordered_iterations) if ordered_iterations else 0) + 1))
        if (
            len(ordered_iterations) < self._config.minimum_critical_iterations
            or ordered_iterations != expected_iterations
        ):
            deviations.append(
                ReleaseDeviation(
                    requirement.runtime_id,
                    scenario.scenario_id,
                    0,
                    "critical_iterations_invalid",
                    tuple(str(value) for value in sorted(iterations)),
                )
            )
        durable = scenario.scenario_id in requirement.durable_scenarios
        required_proofs = {"port", "security", "event", "artifact"}
        if scenario.invariants.required_gates:
            required_proofs.add("approval")
        if scenario.invariants.side_effect_operations:
            required_proofs.add("ledger")
        for category in required_proofs:
            category_applicable[category] = True

        for record in records:
            if record.contract_hash != self._contract_hash:
                deviations.append(
                    ReleaseDeviation(
                        requirement.runtime_id,
                        scenario.scenario_id,
                        record.iteration,
                        "contract_hash_mismatch",
                    )
                )
            if record.runtime_version != requirement.runtime_version:
                deviations.append(
                    ReleaseDeviation(
                        requirement.runtime_id,
                        scenario.scenario_id,
                        record.iteration,
                        "runtime_version_mismatch",
                    )
                )
            if record.capabilities != requirement.capabilities:
                deviations.append(
                    ReleaseDeviation(
                        requirement.runtime_id,
                        scenario.scenario_id,
                        record.iteration,
                        "runtime_capability_claim_mismatch",
                    )
                )
            if record.durable != durable:
                deviations.append(
                    ReleaseDeviation(
                        requirement.runtime_id,
                        scenario.scenario_id,
                        record.iteration,
                        "durable_variant_binding_mismatch",
                    )
                )
            result = self._conformance.evaluate(scenario, record.observation)
            if result.status != "passed":
                deviations.append(
                    ReleaseDeviation(
                        requirement.runtime_id,
                        scenario.scenario_id,
                        record.iteration,
                        "runtime_conformance_failed",
                        tuple(issue.code for issue in result.issues),
                    )
                )
            for category, proof_status in record.proofs.items():
                if proof_status != "not_applicable":
                    category_applicable[category] = True
                if proof_status == "passed" and not _observed_proof_sources(record, category):
                    category_failed[category] = True
                    deviations.append(
                        ReleaseDeviation(
                            requirement.runtime_id,
                            scenario.scenario_id,
                            record.iteration,
                            "proof_source_missing",
                            (category,),
                        )
                    )
                if proof_status in {"failed", "incompatible"}:
                    category_failed[category] = True
                    deviations.append(
                        ReleaseDeviation(
                            requirement.runtime_id,
                            scenario.scenario_id,
                            record.iteration,
                            "reported_invariant_not_green",
                            (category, proof_status),
                        )
                    )
            for category in required_proofs:
                if record.proofs[category] != "passed":
                    category_failed[category] = True
                    deviations.append(
                        ReleaseDeviation(
                            requirement.runtime_id,
                            scenario.scenario_id,
                            record.iteration,
                            f"{category}_invariant_failed",
                            (record.proofs[category],),
                        )
                    )
        return tuple(deviations)

    @staticmethod
    def _capability_coverage_deviations(
        requirement: RuntimeReleaseRequirement,
        records: tuple[RuntimeRunEvidence, ...],
        scenarios: Mapping[str, ReferenceWorkflow],
        category_applicable: dict[str, bool],
        category_failed: dict[str, bool],
    ) -> tuple[ReleaseDeviation, ...]:
        deviations: list[ReleaseDeviation] = []
        scenario_capabilities = {
            capability
            for scenario_id in requirement.required_scenarios
            for capability in scenarios[scenario_id].plan.capabilities
        }
        proof_capabilities = {
            "approval": "approval",
            "tool_calling": "ledger",
            "checkpoint": "checkpoint",
            "resume": "recovery",
            "durability": "recovery",
        }
        for capability in sorted(requirement.capabilities):
            proof = proof_capabilities.get(capability)
            if proof:
                category_applicable[proof] = True
                if any(
                    record.proofs[proof] == "passed"
                    and _observed_proof_sources(record, proof)
                    for record in records
                ):
                    continue
                category_failed[proof] = True
                deviations.append(
                    ReleaseDeviation(
                        requirement.runtime_id,
                        "",
                        0,
                        "reported_capability_not_covered",
                        (capability, proof),
                    )
                )
                continue
            if capability in scenario_capabilities:
                continue
            deviations.append(
                ReleaseDeviation(
                    requirement.runtime_id,
                    "",
                    0,
                    "reported_capability_not_covered",
                    (capability,),
                )
            )
        return tuple(deviations)


__all__ = [
    "EVIDENCE_BUILD_ID_ENV",
    "EVIDENCE_CHAIN_GENESIS",
    "EVIDENCE_COMMAND_HASH_ENV",
    "EVIDENCE_COMMAND_ID_ENV",
    "EVIDENCE_CONTRACT_HASH_ENV",
    "EVIDENCE_PATH_ENV",
    "EVIDENCE_REVISION_ENV",
    "EVIDENCE_RUNTIME_ID_ENV",
    "PROOF_CATEGORIES",
    "ReleaseDeviation",
    "ReleaseVerificationCommand",
    "RuntimeReleaseRequirement",
    "RuntimeRunEvidence",
    "RuntimeEvidenceBinding",
    "RuntimeEvidenceSink",
    "VerificationCommandResult",
    "WorkflowReleaseGateConfig",
    "WorkflowRuntimeGateEvidenceVerifier",
    "WorkflowRuntimeReleaseAdmission",
    "WorkflowRuntimeReleaseGate",
    "WorkflowRuntimeReleaseResult",
    "load_runtime_release_evidence",
    "load_runtime_release_evidence_jsonl",
    "record_runtime_release_evidence",
    "release_verification_command_hash",
    "load_workflow_release_gate_config",
    "workflow_runtime_contract_hash",
]
