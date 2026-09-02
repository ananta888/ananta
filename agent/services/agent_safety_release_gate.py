"""Fail-closed release decision for concrete agent-safety deployments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort

_SOURCE_REF = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")
_RUN_REF = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")


@dataclass(frozen=True, slots=True)
class AgentSafetyEvidenceBinding:
    tenant_id: str
    project_id: str
    task_id: str
    repository_revision: str
    required_scope: str = "local"


class AgentSafetyReleaseGate:
    REQUIRED_LOCAL_GATES = frozenset({"contracts", "security", "chaos", "api", "frontend"})

    def __init__(
        self,
        *,
        allowed_source_refs: set[str] | frozenset[str] | None = None,
        allowed_run_refs: set[str] | frozenset[str] | None = None,
        evidence_registry: EvidenceIdentityRegistryPort | None = None,
    ) -> None:
        self._allowed_source_refs = frozenset(allowed_source_refs or ())
        self._allowed_run_refs = frozenset(allowed_run_refs or ())
        self._evidence_registry = evidence_registry

    def evaluate(
        self,
        *,
        local_gates: Mapping[str, bool],
        containment_available: bool,
        source_refs: list[str],
        run_refs: list[str],
        evidence_binding: AgentSafetyEvidenceBinding | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        missing_local = sorted(gate for gate in self.REQUIRED_LOCAL_GATES if not bool(local_gates.get(gate)))
        if missing_local:
            reasons.append("agent_safety_local_gates_incomplete")
        if not containment_available:
            reasons.append("agent_safety_containment_adapter_unavailable")
        evidence_reasons = self._evidence_reasons(
            source_refs=source_refs,
            run_refs=run_refs,
            binding=evidence_binding,
        )
        reasons.extend(evidence_reasons)
        return {
            "release_allowed": not reasons,
            "state": "passed" if not reasons else "blocked",
            "reason_codes": reasons,
            "local_gates": {key: bool(local_gates.get(key)) for key in sorted(self.REQUIRED_LOCAL_GATES)},
            "containment_available": bool(containment_available),
            "source_refs": list(source_refs) if not reasons else [],
            "run_refs": list(run_refs) if not reasons else [],
            "evidence_reason_code": evidence_reasons[0] if evidence_reasons else "verified",
            "human_intervention_required": False,
        }

    def _evidence_reasons(
        self,
        *,
        source_refs: list[str],
        run_refs: list[str],
        binding: AgentSafetyEvidenceBinding | None,
    ) -> list[str]:
        reasons: list[str] = []
        source_shape_valid = bool(source_refs) and all(_SOURCE_REF.fullmatch(value) for value in source_refs)
        run_shape_valid = len(run_refs) == 1 and all(_RUN_REF.fullmatch(value) for value in run_refs)
        if not source_shape_valid:
            reasons.append("agent_safety_authoritative_source_evidence_unavailable")
        if not run_shape_valid:
            reasons.append("agent_safety_runtime_evidence_unavailable")
        if reasons:
            return reasons
        if self._evidence_registry is None:
            if not set(source_refs).issubset(self._allowed_source_refs):
                reasons.append("agent_safety_authoritative_source_evidence_unavailable")
            if not set(run_refs).issubset(self._allowed_run_refs):
                reasons.append("agent_safety_runtime_evidence_unavailable")
            return reasons
        if binding is None or binding.required_scope not in {"local", "external", "production"}:
            return ["agent_safety_hub_evidence_binding_required"]
        verification = self._evidence_registry.verify_release_binding(
            tenant_id=binding.tenant_id,
            project_id=binding.project_id,
            run_id=run_refs[0],
            required_scope=binding.required_scope,
            task_id=binding.task_id,
            repository_revision=binding.repository_revision,
            source_ids=source_refs,
        )
        if not verification.verified:
            return [f"agent_safety_hub_evidence_unverified:{verification.reason_code}"]
        return []


__all__ = ["AgentSafetyEvidenceBinding", "AgentSafetyReleaseGate"]
