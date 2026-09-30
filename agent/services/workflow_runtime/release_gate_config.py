"""Release-gate configuration model, loader, and contract hash.

Parses and validates ``release_gate.v1.json`` against the reference workflow
catalog and derives the contract hash that binds evidence to one gate/suite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent.services.workflow_evaluation_service import (
    WorkflowEvaluationSuite,
    load_workflow_evaluation_suite,
)
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.conformance import CONFORMANCE_REPORT_SCHEMA
from agent.services.workflow_runtime.execution_plan import EXECUTION_PLAN_SCHEMA
from agent.services.workflow_runtime.reference_workflows import (
    REFERENCE_WORKFLOW_CATALOG_SCHEMA,
    ReferenceWorkflow,
    load_reference_workflows,
)

WORKFLOW_RELEASE_GATE_SCHEMA = "ananta.workflow_runtime_release_gate.v1"
WORKFLOW_RELEASE_RESULT_SCHEMA = "ananta.workflow_runtime_release_gate_result.v1"
DEFAULT_WORKFLOW_RELEASE_GATE_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "workflow_runtime" / "release_gate.v1.json"
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
