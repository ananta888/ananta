"""Release and runtime receipts that fail closed on missing evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort
from ananta_contracts.dendritic_memory import canonical_digest

_SOURCE = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")
_RUN = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")


@dataclass(frozen=True, slots=True)
class DendriticEvidenceBinding:
    tenant_id: str
    project_id: str
    task_id: str
    repository_revision: str
    required_scope: str = "production"


class DendriticMemoryReleaseGate:
    def __init__(self, *, evidence_registry: EvidenceIdentityRegistryPort | None = None) -> None:
        self._evidence_registry = evidence_registry

    def evaluate(
        self,
        *,
        p0_complete: bool,
        ci_green: bool,
        seed_count: int,
        task_family_count: int,
        critical_security_findings: int,
        rollback_verified: bool,
        revoke_verified: bool,
        deletion_verified: bool,
        allowed_source_refs: Sequence[str],
        allowed_run_refs: Sequence[str],
        requested_source_refs: Sequence[str],
        requested_run_refs: Sequence[str],
        evidence_binding: DendriticEvidenceBinding | None = None,
    ) -> dict[str, Any]:
        allowed_sources = set(allowed_source_refs)
        allowed_runs = set(allowed_run_refs)
        sources_valid = (
            bool(requested_source_refs)
            and all(_SOURCE.fullmatch(str(value)) for value in requested_source_refs)
            and (self._evidence_registry is not None or set(requested_source_refs) <= allowed_sources)
        )
        runs_valid = (
            len(requested_run_refs) == 1
            and all(_RUN.fullmatch(str(value)) for value in requested_run_refs)
            and (self._evidence_registry is not None or set(requested_run_refs) <= allowed_runs)
        )
        evidence_reason = "verified"
        if sources_valid and runs_valid and self._evidence_registry is not None:
            if evidence_binding is None or evidence_binding.required_scope not in {
                "local",
                "external",
                "production",
            }:
                sources_valid = runs_valid = False
                evidence_reason = "dendritic_hub_evidence_binding_required"
            else:
                verification = self._evidence_registry.verify_release_binding(
                    tenant_id=evidence_binding.tenant_id,
                    project_id=evidence_binding.project_id,
                    run_id=str(requested_run_refs[0]),
                    required_scope=evidence_binding.required_scope,
                    task_id=evidence_binding.task_id,
                    repository_revision=evidence_binding.repository_revision,
                    source_ids=requested_source_refs,
                )
                sources_valid = runs_valid = verification.verified
                evidence_reason = (
                    "verified"
                    if verification.verified
                    else f"dendritic_hub_evidence_unverified:{verification.reason_code}"
                )
        reasons: list[str] = []
        checks: Mapping[str, bool] = {
            "p0_complete": p0_complete,
            "ci_green": ci_green,
            "seeds_sufficient": seed_count >= 3,
            "task_families_sufficient": task_family_count >= 2,
            "security_clear": critical_security_findings == 0,
            "rollback_verified": rollback_verified,
            "revoke_verified": revoke_verified,
            "deletion_verified": deletion_verified,
            "source_evidence_valid": sources_valid,
            "run_evidence_valid": runs_valid,
        }
        reasons.extend(f"dendritic_release_{name}_failed" for name, passed in checks.items() if not passed)
        result = {
            "eligible": not reasons,
            "reason_codes": reasons,
            "checks": dict(checks),
            "requested_source_refs": list(requested_source_refs) if sources_valid else [],
            "requested_run_refs": list(requested_run_refs) if runs_valid else [],
            "experimental": True,
            "production_eligible": False,
            "claims_verified": sources_valid and runs_valid,
            "evidence_reason_code": evidence_reason,
            "human_intervention_required": False,
        }
        result["receipt_digest"] = canonical_digest(result)
        return result


__all__ = ["DendriticEvidenceBinding", "DendriticMemoryReleaseGate"]
