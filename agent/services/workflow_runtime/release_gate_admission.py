"""Fail-closed consumers of a published release-gate artifact.

``WorkflowRuntimeGateEvidenceVerifier`` validates one artifact and
``WorkflowRuntimeReleaseAdmission`` adapts it to the runtime-selection port.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.reference_workflows import (
    ReferenceWorkflow,
    load_reference_workflows,
)
from agent.services.workflow_runtime.release_gate_config import WORKFLOW_RELEASE_RESULT_SCHEMA


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
