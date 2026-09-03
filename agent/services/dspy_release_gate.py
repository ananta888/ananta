"""Fail-closed DSPy release gate with Hub-registry evidence binding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort

_SOURCE = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")
_RUN = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")


@dataclass(frozen=True, slots=True)
class DspyEvidenceBinding:
    tenant_id: str
    project_id: str
    task_id: str
    repository_revision: str
    required_scope: str = "production"


class DspyReleaseGate:
    REQUIRED = frozenset({"schema", "unit", "security", "integration", "reproducibility", "recovery", "rollback"})

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
        source_refs: list[str],
        run_refs: list[str],
        allowed_source_refs: frozenset[str] | None = None,
        allowed_run_refs: frozenset[str] | None = None,
        evidence_binding: DspyEvidenceBinding | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if any(not local_gates.get(key) for key in self.REQUIRED):
            reasons.append("dspy_local_release_gates_incomplete")
        evidence_reasons = self._evidence_reasons(
            source_refs=source_refs,
            run_refs=run_refs,
            binding=evidence_binding,
            legacy_source_refs=allowed_source_refs,
            legacy_run_refs=allowed_run_refs,
        )
        reasons.extend(evidence_reasons)
        return {
            "release_allowed": not reasons,
            "state": "passed" if not reasons else "blocked",
            "reason_codes": reasons,
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
        binding: DspyEvidenceBinding | None,
        legacy_source_refs: frozenset[str] | None,
        legacy_run_refs: frozenset[str] | None,
    ) -> list[str]:
        if not source_refs or any(not _SOURCE.fullmatch(value) for value in source_refs):
            return ["dspy_source_evidence_unavailable"]
        if len(run_refs) != 1 or any(not _RUN.fullmatch(value) for value in run_refs):
            return ["dspy_run_evidence_unavailable"]
        if self._evidence_registry is None:
            allowed_sources = self._allowed_source_refs | frozenset(legacy_source_refs or ())
            allowed_runs = self._allowed_run_refs | frozenset(legacy_run_refs or ())
            reasons: list[str] = []
            if not set(source_refs).issubset(allowed_sources):
                reasons.append("dspy_source_evidence_unavailable")
            if not set(run_refs).issubset(allowed_runs):
                reasons.append("dspy_run_evidence_unavailable")
            return reasons
        if binding is None or binding.required_scope not in {"local", "external", "production"}:
            return ["dspy_hub_evidence_binding_required"]
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
            return [f"dspy_hub_evidence_unverified:{verification.reason_code}"]
        return []


__all__ = ["DspyEvidenceBinding", "DspyReleaseGate"]
