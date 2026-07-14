"""Deterministic production release gate for workflow runtimes.

The gate consumes normalized evidence emitted by runtime-specific test
harnesses.  It never executes a runtime itself, which keeps framework imports
outside the Hub domain service and makes the evidence verifier reusable by
runtime selection and rollout code.
"""

from __future__ import annotations

import json
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
from agent.services.workflow_runtime.execution_plan import EXECUTION_PLAN_SCHEMA, ExecutionPlan
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

WORKFLOW_RELEASE_GATE_SCHEMA = "ananta.workflow_runtime_release_gate.v1"
WORKFLOW_RELEASE_RESULT_SCHEMA = "ananta.workflow_runtime_release_gate_result.v1"
DEFAULT_WORKFLOW_RELEASE_GATE_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "workflow_runtime" / "release_gate.v1.json"
)
_ROOT = Path(__file__).resolve().parents[3]

# Proofs are accepted only when the persisted observation contains a source
# that can actually demonstrate that proof.  In particular, a non-durable
# runtime may not turn an ephemeral framework checkpoint into production
# checkpoint/recovery evidence.  Native and LangGraph probes therefore emit
# the two Hub-qualified events only after asserting a persisted Hub checkpoint
# can be loaded by a replacement runtime instance.  Temporal may use its
# durable-history events because the record is additionally bound to a durable
# release variant.
_HUB_CHECKPOINT_SOURCE_EVENTS = frozenset(
    {
        "workflow.checkpoint.hub_persisted",
    }
)
_HUB_RECOVERY_SOURCE_EVENTS = frozenset(
    {
        "workflow.checkpoint.hub_restored",
        "workflow.recovery.completed",
    }
)
_DURABLE_CHECKPOINT_SOURCE_EVENTS = frozenset(
    {
        "workflow.checkpoint.created",
    }
)
_DURABLE_RECOVERY_SOURCE_EVENTS = frozenset(
    {
        "workflow.run.resumed",
        "workflow.recovery.completed",
    }
)
_APPROVAL_SOURCE_EVENTS = frozenset(
    {
        "workflow.approval.granted",
        "workflow.run.resumed",
    }
)
_LEDGER_SOURCE_EVENTS = frozenset(
    {
        "workflow.side_effect.completed",
        "workflow.side_effect.failed",
        "workflow.side_effect.uncertain",
    }
)


@dataclass(frozen=True)
class RuntimeReleaseRequirement:
    runtime_id: str
    runtime_version: str
    capabilities: frozenset[str]
    required_scenarios: tuple[str, ...]
    durable_scenarios: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseVerificationCommand:
    command_id: str
    argv: tuple[str, ...]
    evidence_runtime_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowReleaseGateConfig:
    gate_id: str
    gate_version: str
    minimum_critical_iterations: int
    reference_catalog_version: str
    evaluation_suite_version: str
    runtimes: tuple[RuntimeReleaseRequirement, ...]
    verification_commands: tuple[ReleaseVerificationCommand, ...]
    artifact_version: str

    def requirement_for(self, runtime_id: str) -> RuntimeReleaseRequirement:
        for requirement in self.runtimes:
            if requirement.runtime_id == runtime_id:
                return requirement
        raise KeyError(runtime_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "gate_version": self.gate_version,
            "minimum_critical_iterations": self.minimum_critical_iterations,
            "reference_catalog_version": self.reference_catalog_version,
            "evaluation_suite_version": self.evaluation_suite_version,
            "runtimes": {
                requirement.runtime_id: {
                    "runtime_version": requirement.runtime_version,
                    "capabilities": sorted(requirement.capabilities),
                    "required_scenarios": list(requirement.required_scenarios),
                    "durable_scenarios": list(requirement.durable_scenarios),
                }
                for requirement in sorted(self.runtimes, key=lambda item: item.runtime_id)
            },
            "verification_commands": [
                {
                    "command_id": command.command_id,
                    "argv": list(command.argv),
                    "evidence_runtime_ids": list(command.evidence_runtime_ids),
                }
                for command in self.verification_commands
            ],
            "artifact_version": self.artifact_version,
        }


def load_workflow_release_gate_config(
    path: str | Path = DEFAULT_WORKFLOW_RELEASE_GATE_PATH,
    *,
    scenarios: Sequence[ReferenceWorkflow] | None = None,
) -> WorkflowReleaseGateConfig:
    raw = json.loads(Path(path).resolve(strict=True).read_text(encoding="utf-8"))
    if raw.get("schema") != WORKFLOW_RELEASE_GATE_SCHEMA:
        raise ValueError("workflow_release_gate_schema_unsupported")
    if raw.get("gate_version") != "1.0.0":
        raise ValueError("workflow_release_gate_version_unsupported")
    minimum_iterations = int(raw.get("minimum_critical_iterations") or 0)
    if minimum_iterations < 10:
        raise ValueError("workflow_release_gate_iterations_below_ten")
    catalog = tuple(scenarios or load_reference_workflows())
    scenario_by_id = {scenario.scenario_id: scenario for scenario in catalog}

    runtimes_raw = raw.get("runtimes")
    if not isinstance(runtimes_raw, dict) or set(runtimes_raw) != {"native", "langgraph", "temporal"}:
        raise ValueError("workflow_release_gate_runtime_matrix_invalid")
    requirements: list[RuntimeReleaseRequirement] = []
    for runtime_id, item in sorted(runtimes_raw.items()):
        if not isinstance(item, dict):
            raise ValueError("workflow_release_gate_runtime_requirement_invalid")
        if not str(item.get("runtime_version") or ""):
            raise ValueError("workflow_release_gate_runtime_version_required")
        capabilities = _string_set(item.get("capabilities"), "capabilities")
        required = _string_tuple(item.get("required_scenarios"), "required_scenarios")
        durable = _string_tuple(item.get("durable_scenarios"), "durable_scenarios", allow_empty=True)
        if not required or set(required) - set(scenario_by_id) or set(durable) - set(required):
            raise ValueError("workflow_release_gate_scenario_matrix_invalid")
        for scenario_id in required:
            scenario = scenario_by_id[scenario_id]
            if scenario.support_for(runtime_id) != "target":
                raise ValueError("workflow_release_gate_required_scenario_incompatible")
            if set(scenario.plan.capabilities) - set(capabilities):
                raise ValueError("workflow_release_gate_capability_matrix_incomplete")
        requirements.append(
            RuntimeReleaseRequirement(
                runtime_id=runtime_id,
                runtime_version=str(item.get("runtime_version") or ""),
                capabilities=capabilities,
                required_scenarios=required,
                durable_scenarios=durable,
            )
        )

    commands_raw = raw.get("verification_commands")
    if not isinstance(commands_raw, list) or not commands_raw:
        raise ValueError("workflow_release_gate_verification_commands_empty")
    commands: list[ReleaseVerificationCommand] = []
    command_ids: set[str] = set()
    for item in commands_raw:
        if not isinstance(item, dict):
            raise ValueError("workflow_release_gate_verification_command_invalid")
        command_id = str(item.get("command_id") or "")
        argv = _safe_argv(item.get("argv"))
        evidence_runtime_ids = _string_tuple(
            item.get("evidence_runtime_ids"),
            "evidence_runtime_ids",
            allow_empty=True,
        )
        if len(evidence_runtime_ids) > 1:
            raise ValueError("workflow_release_gate_command_multiple_evidence_runtimes")
        if not command_id or command_id in command_ids:
            raise ValueError("workflow_release_gate_verification_command_invalid")
        command_ids.add(command_id)
        commands.append(
            ReleaseVerificationCommand(
                command_id=command_id,
                argv=argv,
                evidence_runtime_ids=evidence_runtime_ids,
            )
        )
    declared_evidence_runtimes = [
        runtime_id for command in commands for runtime_id in command.evidence_runtime_ids
    ]
    if sorted(declared_evidence_runtimes) != sorted(runtimes_raw):
        raise ValueError("workflow_release_gate_evidence_command_matrix_invalid")
    artifact = raw.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("schema") != WORKFLOW_RELEASE_RESULT_SCHEMA:
        raise ValueError("workflow_release_gate_artifact_schema_unsupported")
    if not raw.get("gate_id") or not artifact.get("artifact_version"):
        raise ValueError("workflow_release_gate_identity_required")
    return WorkflowReleaseGateConfig(
        gate_id=str(raw.get("gate_id") or ""),
        gate_version=str(raw.get("gate_version") or ""),
        minimum_critical_iterations=minimum_iterations,
        reference_catalog_version=str(raw.get("reference_catalog_version") or ""),
        evaluation_suite_version=str(raw.get("evaluation_suite_version") or ""),
        runtimes=tuple(requirements),
        verification_commands=tuple(commands),
        artifact_version=str(artifact.get("artifact_version") or ""),
    )


def workflow_runtime_contract_hash(
    config: WorkflowReleaseGateConfig | None = None,
    suite: WorkflowEvaluationSuite | None = None,
) -> str:
    selected_config = config or load_workflow_release_gate_config()
    selected_suite = suite or load_workflow_evaluation_suite()
    return sha256_json(
        {
            "execution_plan_schema": EXECUTION_PLAN_SCHEMA,
            "reference_catalog_schema": REFERENCE_WORKFLOW_CATALOG_SCHEMA,
            "reference_catalog_sha256": selected_suite.catalog_sha256,
            "conformance_report_schema": CONFORMANCE_REPORT_SCHEMA,
            "evaluation_suite_hash": selected_suite.suite_hash,
            "release_gate": selected_config.to_dict(),
        }
    )


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


def _observed_proof_sources(
    record: RuntimeRunEvidence,
    category: str,
) -> tuple[str, ...]:
    """Return persisted observation fields that can substantiate one proof.

    A proof status by itself is intentionally insufficient.  This function is
    also used to render the artifact's ``invariant_evidence`` links, so the
    verifier and an operator can identify the exact command/run that supplied
    a green invariant.
    """

    events = frozenset(str(value) for value in record.observation.event_types)
    if category == "port":
        if not record.observation.terminal_status:
            return ()
        return (
            f"command:{record.command_id}",
            f"terminal:{record.observation.terminal_status}",
        )
    if category == "security":
        return tuple(
            f"policy:{value}"
            for value in sorted(record.observation.policy_decisions)
        )
    if category == "event":
        return tuple(f"event:{value}" for value in sorted(events))
    if category == "artifact":
        return tuple(
            f"artifact:{value}"
            for value in sorted(record.observation.artifact_ids)
        )
    if category == "checkpoint":
        allowed = set(events & _HUB_CHECKPOINT_SOURCE_EVENTS)
        if record.durable:
            allowed.update(events & _DURABLE_CHECKPOINT_SOURCE_EVENTS)
        return tuple(f"event:{value}" for value in sorted(allowed))
    if category == "recovery":
        allowed = set(events & _HUB_RECOVERY_SOURCE_EVENTS)
        if record.durable:
            allowed.update(events & _DURABLE_RECOVERY_SOURCE_EVENTS)
        return tuple(f"event:{value}" for value in sorted(allowed))
    if category == "approval":
        approval_events = events & _APPROVAL_SOURCE_EVENTS
        if not approval_events or not record.observation.gate_ids:
            return ()
        return (
            *(f"event:{value}" for value in sorted(approval_events)),
            *(f"gate:{value}" for value in sorted(record.observation.gate_ids)),
        )
    if category == "ledger":
        ledger_events = events & _LEDGER_SOURCE_EVENTS
        if not ledger_events or not record.observation.side_effect_operations:
            return ()
        return (
            *(f"event:{value}" for value in sorted(ledger_events)),
            *(
                f"operation:{value}"
                for value in sorted(record.observation.side_effect_operations)
            ),
        )
    return ()


class WorkflowRuntimeGateEvidenceVerifier:
    """Fail-closed verifier used before productive selection or rollout."""

    def verify(
        self,
        artifact: Mapping[str, Any],
        *,
        expected_contract_hash: str,
        runtime_id: str,
        required_capabilities: frozenset[str],
        required_scenarios: tuple[str, ...],
    ) -> tuple[bool, str]:
        if artifact.get("schema") != WORKFLOW_RELEASE_RESULT_SCHEMA:
            return False, "release_gate_schema_mismatch"
        if artifact.get("status") != "passed" or artifact.get("deviations") != []:
            return False, "release_gate_not_green"
        if artifact.get("contract_hash") != expected_contract_hash:
            return False, "release_gate_contract_hash_mismatch"
        supplied_hash = str(artifact.get("artifact_hash") or "")
        hash_payload = dict(artifact)
        hash_payload.pop("artifact_hash", None)
        if supplied_hash != sha256_json(hash_payload):
            return False, "release_gate_artifact_hash_mismatch"
        capability_matrix = artifact.get("capability_matrix")
        if not isinstance(capability_matrix, Mapping):
            return False, "release_gate_capability_matrix_missing"
        runtime_capabilities = capability_matrix.get(runtime_id)
        if not isinstance(runtime_capabilities, list) or not required_capabilities.issubset(
            {str(value) for value in runtime_capabilities}
        ):
            return False, "release_gate_capability_incompatible"
        scenario_matrix = artifact.get("scenario_matrix")
        runtime_scenarios = scenario_matrix.get(runtime_id) if isinstance(scenario_matrix, Mapping) else None
        if not isinstance(runtime_scenarios, Mapping):
            return False, "release_gate_runtime_missing"
        if any(
            not isinstance(runtime_scenarios.get(scenario_id), Mapping)
            or runtime_scenarios[scenario_id].get("status") != "passed"
            for scenario_id in required_scenarios
        ):
            return False, "release_gate_scenario_incompatible"
        invariant_matrix = artifact.get("invariant_matrix")
        runtime_invariants = (
            invariant_matrix.get(runtime_id)
            if isinstance(invariant_matrix, Mapping)
            else None
        )
        invariant_evidence = artifact.get("invariant_evidence")
        runtime_invariant_evidence = (
            invariant_evidence.get(runtime_id)
            if isinstance(invariant_evidence, Mapping)
            else None
        )
        if not isinstance(runtime_invariants, Mapping) or not isinstance(
            runtime_invariant_evidence,
            Mapping,
        ):
            return False, "release_gate_invariant_evidence_missing"
        if any(
            status == "passed"
            and (
                not isinstance(runtime_invariant_evidence.get(category), list)
                or not runtime_invariant_evidence[category]
            )
            for category, status in runtime_invariants.items()
        ):
            return False, "release_gate_invariant_evidence_missing"
        commands = artifact.get("verification_commands")
        if (
            not isinstance(commands, list)
            or not commands
            or any(not isinstance(item, Mapping) or item.get("status") != "passed" for item in commands)
        ):
            return False, "release_gate_verification_incomplete"
        return True, "release_gate_verified"


class WorkflowRuntimeReleaseAdmission:
    """Selection-port adapter backed by one immutable release-gate artifact."""

    _GOVERNANCE_PROOFS: Mapping[str, frozenset[str]] = {
        "audit": frozenset({"event", "security"}),
        "authorization": frozenset({"security"}),
        "policy": frozenset({"security"}),
        "side_effect_guard": frozenset({"ledger", "security"}),
    }

    def __init__(
        self,
        *,
        artifact: Mapping[str, Any],
        expected_contract_hash: str,
        runtime_aliases: Mapping[str, str],
        scenarios: Sequence[ReferenceWorkflow] | None = None,
        verifier: WorkflowRuntimeGateEvidenceVerifier | None = None,
    ) -> None:
        if not expected_contract_hash:
            raise ValueError("runtime_release_expected_contract_hash_required")
        self._artifact = deepcopy(dict(artifact))
        self._expected_contract_hash = expected_contract_hash
        self._runtime_aliases = {str(key): str(value) for key, value in runtime_aliases.items()}
        if any(not key or not value for key, value in self._runtime_aliases.items()):
            raise ValueError("runtime_release_alias_invalid")
        self._scenarios = tuple(scenarios or load_reference_workflows())
        self._verifier = verifier or WorkflowRuntimeGateEvidenceVerifier()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        expected_contract_hash: str,
        runtime_aliases: Mapping[str, str],
    ) -> "WorkflowRuntimeReleaseAdmission":
        artifact = json.loads(Path(path).resolve(strict=True).read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            raise ValueError("runtime_release_artifact_invalid")
        return cls(
            artifact=artifact,
            expected_contract_hash=expected_contract_hash,
            runtime_aliases=runtime_aliases,
        )

    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        runtime_id: str,
        runtime_version: str,
        required_capabilities: frozenset[str],
    ) -> tuple[bool, str]:
        canonical_runtime = self._runtime_aliases.get(runtime_id, runtime_id)
        versions = self._artifact.get("versions")
        runtime_versions = versions.get("runtimes") if isinstance(versions, Mapping) else None
        if not isinstance(runtime_versions, Mapping) or runtime_versions.get(canonical_runtime) != runtime_version:
            return False, "runtime_release_gate_runtime_version_mismatch"

        capability_matrix = self._artifact.get("capability_matrix")
        runtime_capabilities = (
            capability_matrix.get(canonical_runtime) if isinstance(capability_matrix, Mapping) else None
        )
        if not isinstance(runtime_capabilities, list):
            return False, "runtime_release_gate_runtime_missing"
        gate_capabilities = frozenset(str(value) for value in runtime_capabilities)
        governance_capabilities = frozenset(self._GOVERNANCE_PROOFS)
        uncovered = required_capabilities - gate_capabilities - governance_capabilities
        if uncovered:
            return False, "runtime_release_gate_capability_not_covered"

        required_invariants = {
            proof
            for capability in required_capabilities & governance_capabilities
            for proof in self._GOVERNANCE_PROOFS[capability]
        }
        invariant_matrix = self._artifact.get("invariant_matrix")
        runtime_invariants = invariant_matrix.get(canonical_runtime) if isinstance(invariant_matrix, Mapping) else None
        if not isinstance(runtime_invariants, Mapping) or any(
            runtime_invariants.get(proof) != "passed" for proof in required_invariants
        ):
            return False, "runtime_release_gate_governance_invariant_failed"

        required_scenarios: set[str] = set()
        for capability in set(plan.capabilities) | set(required_capabilities):
            if capability in governance_capabilities:
                continue
            matches = {
                scenario.scenario_id
                for scenario in self._scenarios
                if scenario.support_for(canonical_runtime) == "target" and capability in scenario.plan.capabilities
            }
            if not matches:
                return False, "runtime_release_gate_scenario_not_covered"
            required_scenarios.update(matches)
        allowed, reason = self._verifier.verify(
            self._artifact,
            expected_contract_hash=self._expected_contract_hash,
            runtime_id=canonical_runtime,
            required_capabilities=required_capabilities & gate_capabilities,
            required_scenarios=tuple(sorted(required_scenarios)),
        )
        return (True, "runtime_release_gate_verified") if allowed else (False, f"runtime_{reason}")


def _string_tuple(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"workflow_release_{field}_invalid")
    normalized = tuple(str(item) for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"workflow_release_{field}_invalid")
    return normalized


def _string_set(value: Any, field: str) -> frozenset[str]:
    return frozenset(_string_tuple(value, field))


def _safe_argv(value: Any) -> tuple[str, ...]:
    argv = _string_tuple(value, "verification_argv")
    if argv[0] not in {"python", "python3"} or any(
        len(item) > 512 or "\x00" in item or item.startswith("/") or item in {"..", "../"} for item in argv
    ):
        raise ValueError("workflow_release_verification_argv_unsafe")
    return argv


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
